import importlib.util
import math
import os
import random
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Event

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError
from PySide6.QtCore import (
    Qt, QTimer, Signal, QThread, QMutex, QMutexLocker, QWaitCondition, QEventLoop,
    QSettings, QStandardPaths, QPointF, QRect, QRectF, QUrl, QEvent, QObject,
    QPropertyAnimation, QEasingCurve, QAbstractAnimation, QVariantAnimation, QSize,
)
from PySide6.QtGui import (
    QAction, QColor, QDesktopServices, QIcon, QImage, QPainter, QPixmap, QRegion,
    QPolygonF, QTransform,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton, QFileDialog,
    QVBoxLayout, QHBoxLayout, QFormLayout, QSlider, QComboBox, QCheckBox,
    QSplitter, QGroupBox, QStatusBar, QDialog, QDialogButtonBox,
    QLineEdit, QScrollArea, QFrame, QListWidget, QGraphicsDropShadowEffect, QStackedWidget, QLayout,
    QGraphicsBlurEffect, QGraphicsOpacityEffect, QToolButton, QSpinBox, QDoubleSpinBox,
)
from PySide6 import __version__ as QT_VERSION

from engine import BokehEngine, EngineSettings, MODE_AI, MODE_MANUAL
from depth_worker import (
    DEFAULT_MODEL, MODEL_SPECS, DepthWorker, model_label, model_spec, resolve_model_path,
    imported_models, add_imported_model, normalize_repo, is_complete_model_dir,
    is_local_ref,
)
from eventlog import (
    EventLog, collect_device_profile, device_signature, timestamp,
)
from history import ActionHistory
from theme import add_card_shadow, apply_theme, asset
from version import __version__ as APP_VERSION
from welcome import WelcomePage
from workspace import WorkspacePage
from design_dialogs import DesignDialog, DesignMessageBox as QMessageBox
from export_completion import ExportCompletionDialog
from session import load_session, save_session, source_signature
from histogram import HistogramModel, HistogramPlot, HistogramPanel
from card_motion import install_card_scroll
from project_help import ProjectHelpBrowser
from window_frame import FramelessWindowFrame

APP_TITLE = "伯爵大光圈模拟"
BRAND_TITLE = "伯爵大光圈"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
# 本次更新的主题标语：跟在界面版本号后面显示，版本号本身不变。
APP_EDITION = "现代主义"
APP_ICON = asset("brand", "app.ico")
MOTION_DEFAULTS = {"enabled": True, "title": True, "tilt": True, "flock": True,
                   "speed": 1.0, "flock_speed": 1.0, "interval": 8, "particles": 24,
                   "tilt_strength": 1.0, "flock_response": 1.0,
                   "flock_varied_speed": True}
MOTION = MOTION_DEFAULTS.copy()

# Match the display overlay's integer midpoint with its RGB tint. OpenCV's
# weighted blend rounds instead of truncating when the midpoint is fractional.
_EFFECT_TINT_LUT = (
    (np.arange(256, dtype=np.uint16)[:, None] + (255, 40, 40)) // 2
).astype(np.uint8).reshape(1, 256, 3)


class _PreciseAnimationMixin:
    """Drive Qt interpolation with a precise timer while preserving elapsed time."""

    def _init_clock(self):
        self._frame_timer = QTimer(self)
        self._frame_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._frame_timer.setInterval(8)
        self._frame_timer.timeout.connect(self._advance_clock)
        self.finished.connect(self._frame_timer.stop)

    def start(self):
        self._frame_timer.stop()
        self._frame_timer.setInterval(MOTION["interval"])
        super().start()
        if self.state() == QAbstractAnimation.State.Running:
            if not MOTION["enabled"]:
                self.setCurrentTime(self.duration())
                return
            self.pause()
            self._started_at = time.monotonic()
            self._frame_timer.start()

    def stop(self):
        self._frame_timer.stop()
        super().stop()

    def _advance_clock(self):
        self.setCurrentTime(min(self.duration(), int((time.monotonic() - self._started_at) * 1000 * MOTION["speed"])))


class SmoothVariantAnimation(_PreciseAnimationMixin, QVariantAnimation):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_clock()


class SmoothPropertyAnimation(_PreciseAnimationMixin, QPropertyAnimation):
    def __init__(self, target, name, parent=None):
        super().__init__(target, name, parent)
        self._init_clock()


def _app_root():
    """Source run -> project root; frozen exe -> the folder holding the exe."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _pick_settings_path():
    """Pick a writable place for ``settings.ini``.

    An INI file rather than the Windows registry: the registry can be locked
    down, and keeping the INI next to the app keeps a portable copy
    self-contained. Falls back to the per-user config folder when the app
    folder turns out to be read-only.
    """
    candidates = [_app_root() / "settings.ini"]
    config_dir = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppConfigLocation
    )
    if config_dir:
        candidates.append(Path(config_dir) / "settings.ini")
    for path in candidates:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            probe = path.parent / f".bjdgq-write-test-{os.getpid()}"
            probe.write_bytes(b"0")
            try:
                probe.unlink()
            except OSError:
                pass
            return path
        except OSError:
            continue
    return candidates[-1]


_SETTINGS_PATH = None


def settings_path():
    """Resolve (once) where the settings file lives."""
    global _SETTINGS_PATH
    if _SETTINGS_PATH is None:
        _SETTINGS_PATH = _pick_settings_path()
    return _SETTINGS_PATH


def set_variant(widget, variant):
    """给控件打 QSS 用的角色标记。

    Qt 只在 polish 时读一次动态属性，控件被布局 polish 之后再改属性不会重绘，
    所以这里显式 unpolish/polish，避免"属性设了但样式没生效"。
    """
    widget.setProperty("variant", variant)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)


def load_image_rgb(path):
    """Read an image through Pillow so Unicode paths and EXIF rotation work."""
    try:
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image)
            return np.array(image.convert("RGB"))
    except UnidentifiedImageError as exc:
        raise ValueError("无法识别的图片格式。") from exc


def save_image_rgb(path, rgb, ext):
    """Write through imencode + tofile; cv2.imwrite cannot handle Unicode paths."""
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    if ext in (".jpg", ".jpeg"):
        params = [cv2.IMWRITE_JPEG_QUALITY, 95]
        codec = ".jpg"
    else:
        params = [cv2.IMWRITE_PNG_COMPRESSION, 3]
        codec = ".png"
    ok, buffer = cv2.imencode(codec, bgr, params)
    if not ok:
        raise IOError("图片编码失败。")
    try:
        buffer.tofile(path)
    except PermissionError:
        raise
    except OSError as exc:
        raise IOError(f"写入文件失败：{exc}") from exc


def save_image_file(path, rgb, fmt, quality=95):
    """Save an RGB image as PNG or JPEG with the requested quality."""
    try:
        image = Image.fromarray(rgb, mode="RGB")
        if fmt == "JPEG":
            image.save(
                path, format="JPEG", quality=int(quality),
                subsampling=0, optimize=True,
            )
        else:
            image.save(path, format="PNG", compress_level=6)
    except PermissionError:
        raise
    except OSError as exc:
        raise IOError(f"写入文件失败：{exc}") from exc


def save_rgba_png(path, rgb, alpha):
    """Save an RGBA PNG: RGB is the effect plate, alpha is the effect mask."""
    rgba = np.dstack([rgb, alpha]).astype(np.uint8)
    try:
        Image.fromarray(rgba, mode="RGBA").save(
            path, format="PNG", compress_level=6
        )
    except PermissionError:
        raise
    except OSError as exc:
        raise IOError(f"写入透明图层失败：{exc}") from exc


def load_depth_map(path):
    """Read a depth map as a float32 array normalized to 0..1."""
    try:
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image)
            if image.mode.startswith("I") or image.mode == "F":
                depth = np.array(image).astype(np.float32)
            else:
                depth = np.array(image.convert("L")).astype(np.float32)
    except UnidentifiedImageError as exc:
        raise ValueError("无法识别的深度图格式。") from exc

    depth = np.nan_to_num(depth, nan=0.0, posinf=0.0, neginf=0.0)
    if depth.ndim == 3:
        depth = depth.mean(axis=2)
    if depth.size == 0:
        raise ValueError("深度图为空。")

    lo, hi = float(depth.min()), float(depth.max())
    if hi > lo:
        depth = (depth - lo) / (hi - lo)
    else:
        depth = np.zeros_like(depth)
    return depth.astype(np.float32)


def save_depth_map(path, depth, ext):
    """Write 16-bit grayscale for PNG/TIFF and 8-bit grayscale for JPEG."""
    depth = np.clip(np.asarray(depth, dtype=np.float32), 0.0, 1.0)
    if ext in (".png", ".tif", ".tiff"):
        array = np.round(depth * 65535.0).astype(np.uint16)
    else:
        array = np.round(depth * 255.0).astype(np.uint8)
    try:
        Image.fromarray(array).save(path)
    except PermissionError:
        raise
    except OSError as exc:
        raise IOError(f"写入深度图失败：{exc}") from exc


@dataclass
class RenderJob:
    """One engine.process() request handed to the render thread."""

    image: object
    depth: object
    settings: object
    mode: int
    kind: str            # "preview" | "export"
    return_layers: bool = False
    job_id: int = 0
    tag: object = None   # identity token for callers that must match a job
    cancelled: Event | None = None


class RenderWorker(QThread):
    """Run BokehEngine.process() off the GUI thread.

    Rendering a full-resolution image takes seconds, so doing it on the GUI
    thread froze the window ("未响应"). Here the engine runs on its own thread
    and only the finished pixels are handed back. Preview requests are
    coalesced and obsolete running previews stop at engine checkpoints.
    Exports keep their submission order and take priority over queued previews.
    """

    finished_job = Signal(object)  # (RenderJob, result, error)

    def __init__(self, engine):
        super().__init__()
        self._engine = engine
        self._mutex = QMutex()
        self._wait = QWaitCondition()
        self._queue = []
        self._active_job = None
        self._seq = 0
        self._closing = False

    def _cancel_previews_locked(self):
        if self._active_job is not None and self._active_job.cancelled is not None:
            self._active_job.cancelled.set()
        self._queue = [job for job in self._queue if job.kind != "preview"]

    def cancel_previews(self):
        with QMutexLocker(self._mutex):
            self._cancel_previews_locked()

    def submit(self, image, depth, settings, mode, kind="preview",
               return_layers=False, tag=None):
        with QMutexLocker(self._mutex):
            if self._closing:
                raise RuntimeError("渲染线程正在关闭。")
            self._seq += 1
            cancelled = Event() if kind == "preview" else None
            job = RenderJob(
                image, depth, settings, mode, kind, return_layers, self._seq, tag, cancelled
            )
            if kind == "preview":
                self._cancel_previews_locked()
                self._queue.append(job)
            else:
                # At most one preview is queued, and it is always last.
                index = len(self._queue)
                if self._queue and self._queue[-1].kind == "preview":
                    index -= 1
                self._queue.insert(index, job)
            job_id = job.job_id
        self._wait.wakeOne()
        return job_id

    def run(self):
        while True:
            self._mutex.lock()
            while not self._queue and not self._closing:
                self._wait.wait(self._mutex)
            if self._closing and not self._queue:
                self._mutex.unlock()
                return
            job = self._queue.pop(0)
            self._active_job = job
            self._mutex.unlock()

            should_cancel = job.cancelled.is_set if job.cancelled is not None else None
            result, error = None, None
            try:
                result = self._engine.process(
                    job.image, job.depth, job.settings,
                    mode=job.mode, return_layers=job.return_layers,
                    should_cancel=should_cancel,
                )
            except Exception as exc:  # noqa: BLE001 - reported to the GUI thread
                error = exc
            with QMutexLocker(self._mutex):
                self._active_job = None
            if should_cancel is None or not should_cancel():
                self.finished_job.emit((job, result, error))

    def stop(self):
        with QMutexLocker(self._mutex):
            if self._active_job is not None and self._active_job.kind != "preview":
                return False
            if any(job.kind != "preview" for job in self._queue):
                return False
            self._closing = True
            self._cancel_previews_locked()
        self._wait.wakeAll()
        self.wait()
        return True


class ZoomableCanvas(QLabel):
    """预览画布基类：Ctrl+滚轮缩放、Ctrl+中键拖拽平移、双击复位。

    缩放/平移只影响显示，不动像素数据；三种预览图（原图、效果图、深度图）
    共用这套交互。普通滚轮不拦截，仍然交给外层滚动区。
    """

    MIN_ZOOM = 0.1
    MAX_ZOOM = 20.0
    ZOOM_HINT = "Ctrl+滚轮缩放 · Ctrl+中键拖拽平移 · 双击复位"
    sourceChanged = Signal(bool)

    def __init__(self, placeholder=""):
        super().__init__(placeholder)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setObjectName("PreviewSurface")
        self._pixmap = None
        self._zoom = 1.0
        self._pan = QPointF(0.0, 0.0)
        self._panning = False
        self._pan_start = QPointF()
        self._pan_origin = QPointF()
        self._idle_cursor = self.cursor()
        self.setToolTip(self.ZOOM_HINT)

    # -- 数据 ---------------------------------------------------------------

    def set_source_pixmap(self, pixmap, placeholder, keep_view=False):
        """换图：默认重置缩放/平移；keep_view=True 时保留当前视图。

        切换「显示原图」、刷新渲染结果都走 keep_view，这样放大看细节时视图不会跳回。
        """
        had_pixmap = self._pixmap is not None
        self._pixmap = pixmap
        if not (keep_view and had_pixmap):
            self._zoom = 1.0
            self._pan = QPointF(0.0, 0.0)
        self.setText("" if pixmap is not None else placeholder)
        self.setPixmap(QPixmap())
        self.update()
        self.sourceChanged.emit(keep_view)

    def reset_view(self):
        self._zoom = 1.0
        self._pan = QPointF(0.0, 0.0)
        self.update()

    # -- 几何 ---------------------------------------------------------------

    def _fit_rect(self):
        """当前缩放/平移下，图片在控件里的目标矩形（逻辑坐标）。"""
        if self._pixmap is None:
            return None
        pw, ph = self._pixmap.width(), self._pixmap.height()
        if pw <= 0 or ph <= 0 or self.width() <= 0 or self.height() <= 0:
            return None
        scale = min(self.width() / pw, self.height() / ph) * self._zoom
        width, height = pw * scale, ph * scale
        return QRectF(
            (self.width() - width) / 2.0 + self._pan.x(),
            (self.height() - height) / 2.0 + self._pan.y(),
            width, height,
        )

    def paintEvent(self, event):
        super().paintEvent(event)  # 背景、边框、占位文字仍由 QLabel 画
        rect = self._fit_rect()
        if rect is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawPixmap(rect, self._pixmap, QRectF(self._pixmap.rect()))

    # -- 交互 ---------------------------------------------------------------

    def _zoom_at(self, anchor, factor):
        """以 anchor 为锚点缩放：该点在图片上的相对位置保持不动。"""
        before = self._fit_rect()
        if before is None:
            return False
        target = float(np.clip(self._zoom * factor, self.MIN_ZOOM, self.MAX_ZOOM))
        if abs(target - self._zoom) < 1e-6:
            return False
        rel_x = (anchor.x() - before.x()) / max(before.width(), 1e-6)
        rel_y = (anchor.y() - before.y()) / max(before.height(), 1e-6)
        self._zoom = target
        after = self._fit_rect()
        self._pan += QPointF(
            anchor.x() - (after.x() + rel_x * after.width()),
            anchor.y() - (after.y() + rel_y * after.height()),
        )
        self.update()
        return True

    def wheelEvent(self, event):
        if self._pixmap is None or not (
            event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            event.ignore()  # 不拦普通滚轮
            return
        delta = event.angleDelta().y()
        if delta:
            self._zoom_at(event.position(), 1.25 ** (delta / 120.0))
        event.accept()

    def mousePressEvent(self, event):
        if (
            self._pixmap is not None
            and event.button() == Qt.MouseButton.MiddleButton
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self._panning = True
            self._pan_start = QPointF(event.position())
            self._pan_origin = QPointF(self._pan)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._panning:
            self._pan = self._pan_origin + (event.position() - self._pan_start)
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._panning and event.button() == Qt.MouseButton.MiddleButton:
            self._panning = False
            self.setCursor(self._idle_cursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if self._pixmap is not None and (
            self._zoom != 1.0 or self._pan != QPointF(0.0, 0.0)
        ):
            self.reset_view()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class ImageCanvas(ZoomableCanvas):
    clicked = Signal(float, float)

    def __init__(self):
        super().__init__("拖入图片，或点击下方「导入图片」")
        self.setMinimumSize(360, 240)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._idle_cursor = Qt.CursorShape.PointingHandCursor
        self.setMouseTracking(True)
        self._flock_target = None
        self._migration_target = None
        self._migration_deadline = 0.0
        self._flock = [[random.random(), random.random(), random.uniform(-1, 1), random.uniform(-1, 1),
                        random.uniform(.65, 1.35), random.randint(140, 255)]
                       for _ in range(MOTION["particles"])]
        self._flock_timer = QTimer(self)
        self._flock_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._flock_timer.setInterval(MOTION["interval"])
        self._flock_last_tick = time.monotonic()
        self._flock_timer.timeout.connect(self._step_flock)
        if MOTION["enabled"] and MOTION["flock"]:
            self._flock_timer.start()

    def _choose_migration_target(self, now):
        """Pick a destination away from the flock's current centre."""
        if not self._flock:
            return
        cx = sum(bird[0] for bird in self._flock) / len(self._flock)
        cy = sum(bird[1] for bird in self._flock) / len(self._flock)
        for _ in range(12):
            target = QPointF(random.uniform(.12, .88), random.uniform(.12, .88))
            if math.hypot(target.x() - cx, target.y() - cy) >= .3:
                break
        self._migration_target = target
        self._migration_deadline = now + random.uniform(7.0, 12.0)

    def _step_flock(self):
        now = time.monotonic()
        elapsed = min(0.08, max(0.0, now - self._flock_last_tick))
        self._flock_last_tick = now
        if self._pixmap is not None or not self.isVisible():
            return
        step = elapsed / .04 * MOTION["flock_speed"]
        points = self._flock
        if not points:
            return
        if self._flock_target is not None:
            target_x = self._flock_target.x() / max(1, self.width())
            target_y = self._flock_target.y() / max(1, self.height())
        else:
            if self._migration_target is None:
                self._choose_migration_target(now)
            centre_x = sum(bird[0] for bird in points) / len(points)
            centre_y = sum(bird[1] for bird in points) / len(points)
            if (now >= self._migration_deadline or
                    math.hypot(centre_x - self._migration_target.x(),
                               centre_y - self._migration_target.y()) < .09):
                self._choose_migration_target(now)
            target_x, target_y = self._migration_target.x(), self._migration_target.y()
        dirty = QRegion()
        for bird in points:
            x, y, vx, vy, individual_speed, opacity = bird
            dirty = dirty.united(QRegion(int(x * self.width()) - 4, int(y * self.height()) - 4, 9, 9))
            neighbours = [other for other in points if other is not bird and
                          (other[0] - x) ** 2 + (other[1] - y) ** 2 < 0.035]
            if neighbours:
                n = len(neighbours)
                vx += (sum(p[2] for p in neighbours) / n - vx) * 0.025 * step
                vy += (sum(p[3] for p in neighbours) / n - vy) * 0.025 * step
                vx += (sum(p[0] for p in neighbours) / n - x) * 0.08 * step
                vy += (sum(p[1] for p in neighbours) / n - y) * 0.08 * step
                for other in neighbours:
                    dx, dy = x - other[0], y - other[1]
                    distance = dx * dx + dy * dy
                    if 0 < distance < 0.0015:
                        vx += dx * 1.5 * step
                        vy += dy * 1.5 * step
            dx, dy = target_x - x, target_y - y
            distance = max(0.001, math.hypot(dx, dy))
            vx += dx / distance * 0.32 * MOTION["flock_response"] * step
            vy += dy / distance * 0.32 * MOTION["flock_response"] * step
            # Smoothstep gives a gentle arrival and faster movement farther away.
            proximity = min(1.0, distance / .35)
            speed_factor = .12 + .88 * proximity * proximity * (3 - 2 * proximity)
            vx += ((0.5 - x) * 0.015 + random.uniform(-0.025, 0.025)) * step
            vy += ((0.5 - y) * 0.015 + random.uniform(-0.025, 0.025)) * step
            speed = max(0.001, math.hypot(vx, vy))
            vx, vy = vx / speed, vy / speed
            individual_factor = individual_speed if MOTION["flock_varied_speed"] else 1.0
            bird[:] = [min(.99, max(.01, x + vx * .009 * step * speed_factor * individual_factor)),
                       min(.99, max(.01, y + vy * .009 * step * speed_factor * individual_factor)),
                       vx, vy, individual_speed, opacity]
            dirty = dirty.united(QRegion(int(bird[0] * self.width()) - 4, int(bird[1] * self.height()) - 4, 9, 9))
        self.update(dirty)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._pixmap is not None or not MOTION["enabled"] or not MOTION["flock"]:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = self.palette().color(self.palette().ColorRole.Highlight)
        painter.setPen(Qt.PenStyle.NoPen)
        for x, y, vx, vy, _individual_speed, opacity in self._flock:
            color.setAlpha(opacity)
            painter.setBrush(color)
            painter.drawEllipse(QPointF(x * self.width(), y * self.height()), 2.1, 2.1)

    def mouseMoveEvent(self, event):
        if self._pixmap is None:
            self._flock_target = event.position()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._flock_target = None
        self._choose_migration_target(time.monotonic())
        super().leaveEvent(event)

    def set_image(self, image, keep_view=False):
        if image is None:
            self._flock_last_tick = time.monotonic()
            if MOTION["enabled"] and MOTION["flock"]:
                self._flock_timer.start()
        else:
            self._flock_timer.stop()
            self._flock_target = None
        if image is None:
            self.set_source_pixmap(None, "打开一张图片开始")
            return
        h, w, ch = image.shape
        qimg = QImage(image.data, w, h, ch * w, QImage.Format.Format_RGB888).copy()
        self.set_source_pixmap(
            QPixmap.fromImage(qimg), "打开一张图片开始", keep_view=keep_view
        )

    def mousePressEvent(self, event):
        if self._pixmap is not None and event.button() == Qt.MouseButton.LeftButton:
            rect = self._fit_rect()
            if rect is not None and rect.width() > 0 and rect.height() > 0:
                x = (event.position().x() - rect.x()) / rect.width()
                y = (event.position().y() - rect.y()) / rect.height()
                if 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0:
                    self.clicked.emit(x, y)
        super().mousePressEvent(event)


def _probe_writable(directory):
    """Return True when we can actually create a file in this directory."""
    try:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / f".bjdgq-write-test-{os.getpid()}"
        probe.write_bytes(b"0")
        probe.unlink()
        return True
    except OSError:
        return False


def choose_export_directory(preferred=None):
    """Pick a writable folder, falling back out of read-only/protected places."""
    candidates = []
    if preferred is not None:
        candidates.append(Path(preferred))
    home = Path.home()
    candidates.extend([
        home / "Downloads",
        home / "Pictures",
        Path(__file__).resolve().parent / "exports",
        Path(tempfile.gettempdir()),
    ])
    for candidate in candidates:
        if _probe_writable(candidate):
            return candidate
    return Path.cwd()


def safe_stem(path):
    """Turn an image name into a filename stem that is valid on Windows."""
    stem = Path(path).stem
    cleaned = "".join(
        "_" if ch in '<>:"/\\|?*' or ord(ch) < 32 else ch for ch in stem
    )
    return cleaned.strip(" .") or "深度图"


def reveal_in_explorer(path):
    """导出完成后在资源管理器里定位并选中该文件；返回是否成功唤起。

    用 explorer.exe 的绝对路径（不依赖 PATH，冻结成 exe 后也可靠），
    失败时退回 QDesktopServices 打开所在文件夹，不再静默吞掉错误。
    """
    if not sys.platform.startswith("win"):
        return False
    target = Path(path)
    if target.exists():
        explorer = Path(os.environ.get("WINDIR", r"C:\Windows")) / "explorer.exe"
        try:
            subprocess.Popen([str(explorer), f"/select,{target}"])
            return True
        except OSError:
            pass
    try:
        return bool(QDesktopServices.openUrl(QUrl.fromLocalFile(str(target.parent))))
    except Exception:
        return False


class DepthPreview(ZoomableCanvas):
    """Small always-visible preview of the current depth map."""

    def __init__(self):
        super().__init__("尚无深度图")
        self.setMinimumHeight(110)

    def set_depth(self, depth):
        if depth is None:
            self.set_source_pixmap(None, "尚无深度图")
            return
        normalised = np.clip(np.asarray(depth, dtype=np.float32), 0.0, 1.0)
        gray = np.ascontiguousarray((normalised * 255.0).astype(np.uint8))
        h, w = gray.shape
        image = QImage(gray.data, w, h, w, QImage.Format.Format_Grayscale8).copy()
        self.set_source_pixmap(QPixmap.fromImage(image), "尚无深度图")


class CardDragReorder(QObject):
    """Drag cards by their title and persist their order."""
    orderChanged = Signal(list)

    def __init__(self, layout, scroll, parent=None):
        super().__init__(parent)
        self.layout, self.scroll = layout, scroll
        self.card = None
        self.origin = None
        self.dragging = False
        self.timer = QTimer(self)
        self.timer.setInterval(35)
        self.timer.timeout.connect(self._autoscroll)
        self.pointer = None
        self._last_pointer_y = None

    def watch(self, card):
        card.installEventFilter(self)
        card.setToolTip("按住卡片标题并上下拖动，可调整卡片顺序。")

    def order(self):
        return [self.layout.itemAt(i).widget().objectName() for i in range(self.layout.count())]

    def apply_order(self, names):
        cards = {self.layout.itemAt(i).widget().objectName(): self.layout.itemAt(i).widget()
                 for i in range(self.layout.count())}
        ordered = [cards.pop(name) for name in names if name in cards] + list(cards.values())
        for card in ordered:
            self.layout.removeWidget(card)
            self.layout.addWidget(card)

    def _autoscroll(self):
        if self.pointer is None:
            return
        y = self.scroll.viewport().mapFromGlobal(self.pointer).y()
        h = self.scroll.viewport().height()
        delta = -max(0, 48 - y) if y < 48 else max(0, y - h + 48)
        if delta:
            bar = self.scroll.verticalScrollBar()
            before = bar.value()
            bar.setValue(before + max(-20, min(20, delta // 3)))
            if bar.value() != before:
                self._move_card(1 if bar.value() > before else -1)

    def _move_card(self, direction):
        """Cross a neighbouring card's midpoint before moving in that direction."""
        if not self.dragging or not direction:
            return
        index = self.layout.indexOf(self.card)
        neighbour_index = index + direction
        if index < 0 or not 0 <= neighbour_index < self.layout.count():
            return
        neighbour = self.layout.itemAt(neighbour_index).widget()
        y = neighbour.mapFromGlobal(self.pointer).y()
        midpoint = neighbour.height() / 2
        if (direction > 0 and y >= midpoint) or (direction < 0 and y <= midpoint):
            self.layout.removeWidget(self.card)
            self.layout.insertWidget(neighbour_index, self.card)

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton and event.position().y() < 34:
            controller=getattr(self.scroll,'_card_scroll',None)
            if controller is not None:controller.cancel()
            for tilt in obj.findChildren(CardTilt):
                tilt._towards(QPointF())
            self.card, self.origin = obj, event.globalPosition().toPoint()
            self._last_pointer_y = self.origin.y()
            self.dragging = False
            return True
        if self.card is obj and kind == QEvent.Type.MouseMove:
            self.pointer = event.globalPosition().toPoint()
            direction = (self.pointer.y() > self._last_pointer_y) - (self.pointer.y() < self._last_pointer_y)
            self._last_pointer_y = self.pointer.y()
            if not self.dragging and (self.pointer - self.origin).manhattanLength() >= QApplication.startDragDistance():
                self.dragging = True
                obj.setProperty("drag", "true")
                obj.style().unpolish(obj); obj.style().polish(obj)
                obj.setCursor(Qt.CursorShape.ClosedHandCursor)
                self.timer.start()
            if self.dragging:
                self._move_card(direction)
            return True
        if self.card is obj and kind == QEvent.Type.MouseButtonRelease:
            self.timer.stop()
            if self.dragging:
                obj.setProperty("drag", "false")
                obj.style().unpolish(obj); obj.style().polish(obj)
                obj.unsetCursor()
                self.orderChanged.emit(self.order())
            self.card = self.origin = self.pointer = None
            self._last_pointer_y = None
            self.dragging = False
            return True
        return False


class HoverLift(QObject):
    def __init__(self, widget, parent=None):
        super().__init__(parent)
        self.effect = QGraphicsDropShadowEffect(widget)
        self.effect.setOffset(0, 3)
        self.effect.setBlurRadius(0)
        widget.setGraphicsEffect(self.effect)
        widget.installEventFilter(self)
        self.animation = SmoothPropertyAnimation(self.effect, b"blurRadius", self)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.Enter, QEvent.Type.Leave):
            self.animation.stop()
            self.animation.setDuration(220 if event.type() == QEvent.Type.Enter else 160)
            self.animation.setStartValue(self.effect.blurRadius())
            self.animation.setEndValue(20 if event.type() == QEvent.Type.Enter and obj.isEnabled() else 0)
            self.animation.start()
        return False


class CardTiltShadow(QGraphicsDropShadowEffect):
    """Tilt a card as a single painted surface while retaining its shadow."""

    def __init__(self, card):
        super().__init__(card)
        self.tilt = QPointF()
        self.setBlurRadius(28)
        self.setOffset(0, 6)
        color = QColor("#8C7B6B")
        color.setAlpha(26)
        self.setColor(color)

    def draw(self, painter):
        rect = self.sourceBoundingRect()
        if rect.isEmpty():
            super().draw(painter)
            return
        # Keep all four corners inside the original rectangle: Qt can then
        # composite the effect without clipping or changing widget geometry.
        dx = self.tilt.x() * 20.0 * MOTION["tilt_strength"]
        dy = self.tilt.y() * 20.0 * MOTION["tilt_strength"]
        left, top, right, bottom = rect.left(), rect.top(), rect.right(), rect.bottom()
        source = QPolygonF([rect.topLeft(), rect.topRight(), rect.bottomRight(), rect.bottomLeft()])
        destination = QPolygonF([
            QPointF(left + max(0, dx), top + max(0, dy)),
            QPointF(right - max(0, -dx), top + max(0, -dy)),
            QPointF(right - max(0, dx), bottom - max(0, -dy)),
            QPointF(left + max(0, -dx), bottom - max(0, dy)),
        ])
        transform = QTransform()
        if not QTransform.quadToQuad(source, destination, transform):
            super().draw(painter)
            return
        painter.save()
        painter.setTransform(transform, True)
        super().draw(painter)
        painter.restore()


class CardTilt(QWidget):
    """Track pointer position and animate one card's painted surface."""

    def __init__(self, widget):
        super().__init__(widget)
        self.card = widget
        self.effect = CardTiltShadow(widget)
        widget.setGraphicsEffect(self.effect)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setGeometry(widget.rect())
        self.offset = QPointF()
        self.animation = SmoothVariantAnimation(self)
        self.animation.setDuration(180)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.valueChanged.connect(self._set_offset)
        for child in [widget] + widget.findChildren(QWidget):
            if child is not self:
                child.setMouseTracking(True)
                child.installEventFilter(self)
        self.show()
        self.raise_()

    def _set_offset(self, value):
        self.offset = value
        self.effect.tilt = value
        self.effect.update()
        self.update()

    def _towards(self, point):
        if not MOTION["enabled"] or not MOTION["tilt"]:
            self.animation.stop()
            if not self.offset.isNull():
                self._set_offset(QPointF())
            return
        self.animation.stop()
        self.animation.setStartValue(self.offset)
        self.animation.setEndValue(point)
        self.animation.start()

    def eventFilter(self, obj, event):
        if obj is self.card and event.type() == QEvent.Type.Resize:
            self.setGeometry(self.card.rect())
        if event.type() in (QEvent.Type.MouseMove, QEvent.Type.Enter) and obj.isEnabled() and not QApplication.mouseButtons():
            from PySide6.QtGui import QCursor
            pos = self.card.mapFromGlobal(QCursor.pos())
            x = pos.x() / max(1, self.card.width()) - .5
            y = pos.y() / max(1, self.card.height()) - .5
            self._towards(QPointF(max(-.5, min(.5, x)), max(-.5, min(.5, y))))
        elif event.type() == QEvent.Type.Leave and obj is self.card:
            self._towards(QPointF())
        elif event.type() == QEvent.Type.MouseButtonPress:
            self._towards(QPointF())
        return False

    def paintEvent(self, event):
        pass


class ButtonRipple(QWidget):
    """A brief, mouse-transparent press response drawn over a control."""

    def __init__(self, button):
        super().__init__(button)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setGeometry(button.rect())
        self._origin = QPointF()
        self._progress = 1.0
        self.color = QColor("#FFFFFF")
        self.animation = SmoothVariantAnimation(self)
        self.animation.setDuration(360)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.valueChanged.connect(self._advance)
        button.installEventFilter(self)
        self.show()

    def _advance(self, value):
        self._progress = value
        self.update()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Resize:
            self.setGeometry(obj.rect())
        elif (event.type() == QEvent.Type.MouseButtonPress and obj.isEnabled()
              and event.button() == Qt.MouseButton.LeftButton):
            self._origin = event.position()
            self.animation.stop()
            self._progress = 0.0
            self.animation.start()
        elif (event.type() == QEvent.Type.KeyPress and obj.isEnabled()
              and not event.isAutoRepeat()
              and event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter)):
            self._origin = QPointF(self.width() / 2, self.height() / 2)
            self.animation.stop()
            self._progress = 0.0
            self.animation.start()
        return False

    def paintEvent(self, event):
        if self._progress >= 1:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(self.color)
        color.setAlpha(int(65 * (1 - self._progress)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        radius = (self.width() ** 2 + self.height() ** 2) ** 0.5 * self._progress
        painter.drawEllipse(self._origin, radius, radius)


class ExportDialog(DesignDialog):
    """Export options: content, format, resolution, anti-aliasing and quality."""

    def __init__(self, parent, width, height):
        super().__init__(parent)
        self.setWindowTitle("导出设置")
        self.setMinimumWidth(500)
        self.resize(600, 520)
        self._image_width = width
        self._image_height = height

        form = QFormLayout(self.body)
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(20);form.setVerticalSpacing(14)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self.type_combo = QComboBox()
        self.type_combo.addItem("合成效果图", False)
        self.type_combo.addItem("大光圈效果图层（透明 PNG）", True)
        form.addRow("导出内容", self.type_combo)

        self.format_combo = QComboBox()
        self.format_combo.addItem("PNG（无损）", "PNG")
        self.format_combo.addItem("JPEG（有损）", "JPEG")
        form.addRow("图片格式", self.format_combo)

        self.scale_combo = QComboBox()
        for label, scale in [
            ("100% 原图", 1.0), ("75%", 0.75), ("50%", 0.5), ("25%", 0.25)
        ]:
            self.scale_combo.addItem(label, scale)
        form.addRow("输出分辨率", self.scale_combo)

        self.aa_combo = QComboBox()
        self.aa_combo.addItem("标准（1×）", 1)
        self.aa_combo.addItem("高质量（2× 超采样）", 2)
        self.aa_combo.addItem("极致（3× 超采样）", 3)
        self.aa_combo.setCurrentIndex(1)
        form.addRow("光斑抗锯齿", self.aa_combo)

        self.quality_slider = QSlider(Qt.Orientation.Horizontal)
        self.quality_slider.setRange(60, 100)
        self.quality_slider.setValue(95)
        self.quality_value = QLabel("95")
        quality_row = QWidget()
        quality_layout = QHBoxLayout(quality_row)
        quality_layout.setContentsMargins(0, 0, 0, 0)
        quality_layout.addWidget(self.quality_slider, 1)
        quality_layout.addWidget(self.quality_value)
        form.addRow("JPEG 质量", quality_row)

        self.size_label = QLabel()
        form.addRow("输出尺寸", self.size_label)

        self.layer_note = QLabel(
            "透明图层仅包含虚化和光斑效果，叠加至原图后可还原效果；仅支持 PNG 格式。"
        )
        self.layer_note.setWordWrap(True)
        self.layer_note.setProperty("role", "hint")
        form.addRow(self.layer_note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("导出")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        set_variant(buttons.button(QDialogButtonBox.StandardButton.Ok), "primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.set_footer(buttons)

        self.type_combo.currentIndexChanged.connect(self._refresh)
        self.format_combo.currentIndexChanged.connect(self._refresh)
        self.scale_combo.currentIndexChanged.connect(self._refresh)
        self.quality_slider.valueChanged.connect(
            lambda value: self.quality_value.setText(str(value))
        )
        self._refresh()

    def _refresh(self):
        layer = bool(self.type_combo.currentData())
        if layer:
            index = self.format_combo.findData("PNG")
            if index >= 0:
                self.format_combo.setCurrentIndex(index)
            self.format_combo.setEnabled(False)
        else:
            self.format_combo.setEnabled(True)

        self.quality_slider.setEnabled(
            self.format_combo.currentData() == "JPEG" and not layer
        )
        self.layer_note.setVisible(layer)

        scale = float(self.scale_combo.currentData())
        width = max(1, int(round(self._image_width * scale)))
        height = max(1, int(round(self._image_height * scale)))
        self.size_label.setText(f"{width} × {height}")

    def values(self):
        layer = bool(self.type_combo.currentData())
        return {
            "layer": layer,
            "format": "PNG" if layer else self.format_combo.currentData(),
            "scale": float(self.scale_combo.currentData()),
            "aa": int(self.aa_combo.currentData()),
            "quality": int(self.quality_slider.value()),
        }


class ModelImportDialog(DesignDialog):
    """Import a depth model: a local HF-format folder, or a repo id / link."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("导入 AI 深度模型")
        self.setMinimumWidth(560)
        self.resize(640, 480)

        form = QFormLayout(self.body)
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(20);form.setVerticalSpacing(14)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("可留空，默认使用文件夹名称或仓库名称")
        form.addRow("显示名称", self.name_edit)

        local_row = QWidget()
        local_layout = QHBoxLayout(local_row)
        local_layout.setContentsMargins(0, 0, 0, 0)
        self.local_edit = QLineEdit()
        self.local_edit.setPlaceholderText("本地已下载的模型文件夹")
        self.local_edit.textEdited.connect(self._local_edited)
        browse = QPushButton("浏览…")
        browse.clicked.connect(self._browse)
        local_layout.addWidget(self.local_edit, 1)
        local_layout.addWidget(browse)
        form.addRow("本地模型文件夹", local_row)

        self.repo_edit = QLineEdit()
        self.repo_edit.setPlaceholderText("如 depth-anything/Depth-Anything-V2-Base-hf 或模型页面链接")
        self.repo_edit.textEdited.connect(self._repo_edited)
        form.addRow("仓库标识或链接", self.repo_edit)

        note = QLabel(
            "请选择本地模型文件夹，或填写仓库标识、模型页面链接。"
            "本地文件夹中的模型将直接使用；仓库模型在本地不存在时，运行 AI 估算会联网下载"
            "（依次尝试 hf-mirror 与 huggingface.co，支持断点续传）。"
        )
        note.setWordWrap(True)
        note.setProperty("role", "hint")
        form.addRow(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("导入")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        set_variant(buttons.button(QDialogButtonBox.StandardButton.Ok), "primary")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        self.set_footer(buttons)

    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, "选择模型文件夹")
        if path:
            self.local_edit.setText(path)
            self.repo_edit.clear()

    def _local_edited(self, *_):
        if self.local_edit.text().strip():
            self.repo_edit.clear()

    def _repo_edited(self, *_):
        if self.repo_edit.text().strip():
            self.local_edit.clear()

    def _accept(self):
        kind, _value, _name = self.values()
        if kind is None:
            QMessageBox.information(
                self, "提示",
                "请填写本地模型文件夹、仓库标识或模型页面链接。",
            )
            return
        self.accept()

    def values(self):
        """Return ``(kind, value, name)`` with kind ``"local"`` / ``"repo"``."""
        local = self.local_edit.text().strip()
        repo = self.repo_edit.text().strip()
        name = self.name_edit.text().strip()
        if local and not repo:
            return ("local", local, name)
        if repo and not local:
            return ("repo", repo, name)
        return (None, "", name)


# 参与"操作记录"的控件：(属性名, "value" 取值 / "index" 取选项下标 / "checked" 取勾选)
_BASE_F_STOPS = (1.2, 1.4, 1.8, 2.0, 2.8, 4.0, 5.6, 8.0)
_EXTENDED_F_STOPS = (0.95, 1.0, 11.0, 16.0)
_EXTENDED_BLADES = (3, 4, 10, 12, 16)
_FOCUS_VALUE_SCALE = 10
_BOKEH_RANGES = {
    "blur_slider": ((0, 100), (0, 200)),
    "bokeh_slider": ((0, 100), (0, 250)),
    "bokeh_size": ((30, 200), (5, 600)),
    "bokeh_density": ((0, 100), (0, 200)),
    "spherical_slider": ((-100, 100), (-200, 200)),
    "cat_eye_slider": ((0, 100), (0, 160)),
    "depth_strength": ((0, 100), (0, 250)),
}

_HISTORY_WIDGETS = (
    ("aperture", "index"),
    ("blur_slider", "value"),
    ("bokeh_slider", "value"),
    ("bokeh_shape", "index"),
    ("bokeh_density", "value"),
    ("bokeh_size", "value"),
    ("full_luminance_bokeh", "checked"),
    ("spherical_slider", "value"),
    ("cat_eye_slider", "value"),
    ("highlight_check", "checked"),
    ("highlight_slider", "value"),
    ("focus_slider", "value"),
    ("depth_strength", "value"),
    ("subject_expand_slider", "value"),
    ("subject_feather_slider", "value"),
    ("preview_scale", "index"),
    ("depth_mode", "index"),
    ("depth_size", "index"),
    ("model_combo", "index"),
)


class EditorialBackground(QWidget):
    """Reference artwork remains behind the interactive editing surfaces."""

    def __init__(self):
        super().__init__()
        self.dark = False
        self.reference = QPixmap(str(asset("backgrounds", "reference.png")))

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.dark:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        if not self.reference.isNull():
            scale = min(self.width() / self.reference.width(), self.height() / self.reference.height())
            width, height = self.reference.width() * scale, self.reference.height() * scale
            target = QRectF((self.width() - width) / 2, (self.height() - height) / 2, width, height)
            painter.drawPixmap(target, self.reference, QRectF(self.reference.rect()))


class FloatingPanelWindow(DesignDialog):
    """A non-modal, resizable window that returns its panel when closed."""

    def __init__(self, title, on_close, parent):
        super().__init__(parent, flags=Qt.WindowType.Window)
        self.setWindowTitle(title)
        self._on_close = on_close
        self.finished.connect(lambda _result:self._on_close())
        self.setMinimumSize(300, 220)
        self.resize(520, 520)

    def closeEvent(self, event):
        self._on_close()
        super().closeEvent(event)


class FloatingPanelController(QObject):
    def __init__(self, panel, container, title, window, reorder=None):
        super().__init__(window)
        self.panel, self.container, self.window = panel, container, window
        self.title, self.reorder = title, reorder
        self.floating = None
        self.placeholder = None
        self.button = QToolButton(panel)
        self.button.setText("↗")
        self.button.setToolTip("在独立窗口中打开；关闭窗口后恢复原位")
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button.setFixedSize(24, 24)
        self.button.clicked.connect(self.detach)
        self.button.hide()
        panel.setMouseTracking(True)
        panel.installEventFilter(self)
        self._position_button()

    def _position_button(self):
        self.button.move(max(0, self.panel.width() - self.button.width() - 8), 6)
        self.button.raise_()

    def eventFilter(self, obj, event):
        if obj is self.panel:
            if event.type() == QEvent.Type.Resize:
                self._position_button()
            elif event.type() == QEvent.Type.Enter and self.floating is None:
                self._position_button()
                self.button.show()
            elif event.type() == QEvent.Type.Leave:
                self.button.hide()
        return False

    def detach(self):
        if self.floating is not None:
            self.floating.raise_()
            return
        original_name = self.panel.objectName()
        placeholder = QGroupBox(self.title) if self.reorder else QWidget()
        placeholder.setObjectName(original_name)
        layout = QVBoxLayout(placeholder)
        restore = QPushButton("已在独立窗口打开 · 恢复原位")
        layout.addWidget(restore)
        placeholder.setMinimumHeight(80)
        if isinstance(self.container, QSplitter):
            index = self.container.indexOf(self.panel)
            if index < 0:
                return
            sizes = self.container.sizes()
            self.container.replaceWidget(index, placeholder)
            self.container.setSizes(sizes)
        else:
            if self.container.indexOf(self.panel) < 0:
                return
            self.container.replaceWidget(self.panel, placeholder)
            self.reorder.watch(placeholder)
        self.placeholder = placeholder
        dialog = FloatingPanelWindow(self.title, self.restore, self.window)
        self.floating = dialog
        float_layout = QVBoxLayout(dialog.body)
        float_layout.setContentsMargins(8, 8, 8, 8)
        if self.reorder:
            scroll = QScrollArea(dialog)
            scroll.setWidgetResizable(True)
            scroll.setWidget(self.panel)
            install_card_scroll(scroll,MOTION)
            float_layout.addWidget(scroll)
        else:
            float_layout.addWidget(self.panel)
        self.button.hide()
        restore.clicked.connect(dialog.close)
        dialog.show()
        self.panel.show()
        placeholder.show()

    def restore(self):
        if self.floating is None or self.placeholder is None:
            return
        placeholder, dialog = self.placeholder, self.floating
        self.panel.setParent(None)
        if isinstance(self.container, QSplitter):
            index = self.container.indexOf(placeholder)
            if index >= 0:
                sizes = self.container.sizes()
                self.container.replaceWidget(index, self.panel)
                self.container.setSizes(sizes)
        elif self.container.indexOf(placeholder) >= 0:
            self.container.replaceWidget(placeholder, self.panel)
        placeholder.deleteLater()
        self.placeholder = None
        self.floating = None
        self.panel.show()
        dialog.deleteLater()


class PageStack(QStackedWidget):
    """Let each page define its own minimum rather than all hidden pages."""

    def __init__(self,parent=None):
        super().__init__(parent)
        self.layout().setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.currentChanged.connect(lambda _index:self.updateGeometry())

    def minimumSizeHint(self):
        page=self.currentWidget()
        if page is None:return QSize(0,0)
        return page.minimumSizeHint().expandedTo(page.minimumSize()).expandedTo(QSize(0,0))

    def sizeHint(self):
        page=self.currentWidget()
        if page is None:return QSize(0,0)
        return page.sizeHint().expandedTo(self.minimumSizeHint())


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        MOTION.update(MOTION_DEFAULTS)
        motion_store = QSettings(str(settings_path()), QSettings.Format.IniFormat)
        for key in ("enabled", "title", "tilt", "flock", "flock_varied_speed"):
            MOTION[key] = str(motion_store.value("motion/" + key, str(MOTION[key]))).lower() in ("true", "1", "yes")
        for key, choices in (("speed", (0.5, 1.0, 1.5, 2.0)),
                             ("flock_speed", (0.5, 1.0, 1.5, 2.0)),
                             ("tilt_strength", (0.5, 1.0, 1.5)),
                             ("flock_response", (0.5, 1.0, 1.5)),
                             ("interval", (8, 16, 33)), ("particles", (12, 24, 40))):
            try:
                value = type(MOTION[key])(motion_store.value("motion/" + key, MOTION[key]))
                if value in choices:
                    MOTION[key] = value
            except (TypeError, ValueError):
                pass
        self._theme_mode = str(QSettings(str(settings_path()), QSettings.Format.IniFormat).value("theme", "light"))
        if self._theme_mode not in ("light", "dark"):
            self._theme_mode = "light"
        apply_theme(QApplication.instance(), self._theme_mode)
        self.setWindowTitle(f"{APP_TITLE}  v{APP_VERSION} · {APP_EDITION}")
        self.resize(1280, 800)
        self.setAcceptDrops(True)
        self.engine = BokehEngine()
        # 事件日志：每次处理的参数、耗时与设备信息都写进 logs/，方便排查问题。
        self.event_log = EventLog(settings_path().parent / "logs")
        self._render_started = None
        self._ai_started = None
        self.event_log.log(
            "app.start",
            session=self.event_log.session,
            version=APP_VERSION,
            frozen=getattr(sys, "frozen", False),
            python=sys.version.split()[0],
            qt=QT_VERSION,
            backend=self.engine.backend_description,
            log=str(self.event_log.path),
        )
        self.original = None
        self.original_full = None
        self.processed = None
        self.depth = None
        self._depth_full = None
        self._depth_name = None
        self.depth_worker = None
        self._ai_generation = 0
        self.image_path = None
        self._image_source_signature = None
        self._workspace_entered = False
        self._resume_state = None
        self._resume_history = None
        self._welcome_action_states = []

        # All engine.process() work runs on this thread so a slow full-size
        # render can never block the window (which used to show "未响应").
        self.render_worker = RenderWorker(self.engine)
        self.render_worker.finished_job.connect(self.on_render_finished)
        self.render_worker.start()
        self._preview_job = 0
        self._export_waiters = 0
        self._closing = False
        self._device_profile_logged = False

        # Coalesce rapid slider events instead of rendering for every pixel move.
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(90)
        self._render_timer.timeout.connect(self.render)

        central = EditorialBackground()
        central.dark = self._theme_mode == "dark"
        self.editorial_background = central
        self.pages = PageStack(self)
        self.pages.addWidget(central)
        self.setCentralWidget(self.pages)
        outer = QHBoxLayout(central)
        self._editorial_outer = outer
        outer.setContentsMargins(56, 72, 52, 16)
        outer.setSpacing(22)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("MainSplitter")
        splitter.setHandleWidth(20)
        self._main_splitter = splitter
        outer.addWidget(splitter)

        preview_panel = QWidget()
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(10)
        self.theme_btn = QPushButton()
        self.theme_btn.clicked.connect(self.toggle_theme)
        self.motion_btn = QPushButton("动画设置")
        self.motion_btn.setToolTip("调整动画开关、速度和性能")
        self.motion_btn.clicked.connect(self.show_motion_settings)
        self._update_theme_button()
        self.status_label = QLabel("就绪 · 自动检测 GPU")
        self.status_label.setProperty("role", "status")
        self._title_timer = QTimer(self)
        self._title_timer.setInterval(150)
        self._title_timer.timeout.connect(self._type_title_step)
        self._title_index = 0

        canvases = QSplitter(Qt.Orientation.Horizontal)
        self.before_canvas = ImageCanvas()
        self.after_canvas = ImageCanvas()
        self.before_canvas.setToolTip(f"原图预览 · {ZoomableCanvas.ZOOM_HINT}")
        self.after_canvas.setToolTip(
            f"效果预览（点击可设置焦点）· {ZoomableCanvas.ZOOM_HINT}"
        )
        self.before_canvas.clicked.connect(self.on_canvas_clicked)
        self.after_canvas.clicked.connect(self.on_canvas_clicked)

        right_side = QSplitter(Qt.Orientation.Vertical)
        effect_panel = QWidget()
        effect_layout = QVBoxLayout(effect_panel)
        effect_layout.setContentsMargins(0, 0, 0, 0)
        self.effect_title = QLabel("效果图", self.after_canvas)
        self.effect_title.setProperty("role", "previewTag")
        self.effect_title.setFixedSize(80, 24)
        self.effect_title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.effect_title.move(1, 1)
        self.effect_title.show()
        effect_layout.addWidget(self.after_canvas, 1)
        right_side.addWidget(effect_panel)
        depth_panel = QWidget()
        depth_layout = QVBoxLayout(depth_panel)
        depth_layout.setContentsMargins(0, 0, 0, 0)
        depth_layout.setSpacing(0)
        self.depth_preview = DepthPreview()
        depth_title = QLabel("深度图", self.depth_preview)
        depth_title.setProperty("role", "previewTag")
        depth_title.setFixedSize(80, 24)
        depth_title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        depth_title.move(1, 1)
        depth_title.show()
        depth_layout.addWidget(self.depth_preview, 1)
        right_side.addWidget(depth_panel)
        right_side.setCollapsible(0, False)
        right_side.setCollapsible(1, False)
        right_side.setSizes([400, 400])

        original_panel = QWidget()
        original_layout = QVBoxLayout(original_panel)
        original_layout.setContentsMargins(0, 0, 0, 0)
        original_title = QLabel("原图", self.before_canvas)
        original_title.setProperty("role", "previewTag")
        original_title.setFixedSize(80, 24)
        original_title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        original_title.move(1, 1)
        original_title.show()
        original_layout.addWidget(self.before_canvas, 1)
        canvases.addWidget(original_panel)
        canvases.addWidget(right_side)
        canvases.setSizes([470, 470])
        preview_layout.addWidget(canvases, 1)

        bottom = QHBoxLayout()
        self.preview_actions_layout = bottom
        bottom.setContentsMargins(2, 4, 2, 0)
        bottom.setSpacing(8)
        self.brand_overlay = QWidget(central)
        self.brand_overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        brand_block = QVBoxLayout(self.brand_overlay)
        brand_block.setContentsMargins(0, 0, 0, 0)
        brand_block.setSpacing(0)
        mark = QLabel("EDM")
        self.editorial_mark = mark
        mark.setObjectName("EditorialMark")
        brand_block.addWidget(mark)
        brand_plate = QFrame()
        brand_plate.setObjectName("EditorialBrand")
        self.editorial_plate = brand_plate
        brand_row = QHBoxLayout(brand_plate)
        brand_row.setContentsMargins(14, 5, 14, 5)
        brand_name = QLabel("伯爵大光圈")
        brand_name.setObjectName("EditorialBrandText")
        self.editorial_brand = brand_name
        self.title_label = brand_name
        brand_row.addWidget(brand_name)
        brand_row.addStretch()
        diamond = QLabel("◇")
        diamond.setObjectName("EditorialDiamond")
        brand_row.addWidget(diamond)
        brand_block.addWidget(brand_plate)
        self.brand_gap = QWidget()
        self.brand_gap.setFixedWidth(185)
        bottom.addWidget(self.brand_gap)
        self.brand_overlay.show()
        self.open_btn = QPushButton("导入图片")
        self.open_btn.clicked.connect(self.open_image)
        set_variant(self.open_btn, "warm")
        self.compare_btn = QPushButton("显示原图")
        self.compare_btn.setCheckable(True)
        self.compare_btn.setToolTip("切换右侧预览中的原图与效果图")
        self.compare_btn.toggled.connect(self.toggle_original)
        self.export_btn = QPushButton("导出图片")
        self.export_btn.setObjectName("ExportImageButton")
        self.open_btn.setObjectName("OpenImageButton")
        self.compare_btn.setObjectName("CompareImageButton")
        self.export_btn.clicked.connect(self.export_image)
        set_variant(self.export_btn, "primary")
        self._overlay_buttons = (self.open_btn, self.compare_btn, self.export_btn)
        for button in self._overlay_buttons:
            button.setParent(central)
            button.show()
        self._hover_lift = [HoverLift(button, self) for button in
                            (self.open_btn, self.export_btn, self.theme_btn)]
        self._ripples = [ButtonRipple(button) for button in
                         (self.open_btn, self.export_btn, self.compare_btn)]
        self._update_glow()
        bottom.addStretch()
        preview_layout.addLayout(bottom)
        splitter.addWidget(preview_panel)

        controls = QWidget()
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(14)

        cards_host = QWidget()
        cards_layout = QVBoxLayout(cards_host)
        cards_layout.setContentsMargins(0, 0, 0, 0)
        cards_layout.setSpacing(14)
        controls_layout.addWidget(cards_host)

        camera_box = QGroupBox("镜头与虚化")
        camera_form = QFormLayout(camera_box)
        self.aperture = QComboBox()
        for stop in _BASE_F_STOPS:
            self.aperture.addItem(f"f/{stop}", stop)
        self.aperture.setCurrentIndex(1)
        self.aperture.currentIndexChanged.connect(self.on_settings_changed)
        camera_form.addRow("模拟光圈", self.aperture)

        self.blur_slider, self.blur_value = self.make_slider(0, 100, 55, editable=True)
        self.blur_slider.valueChanged.connect(self.on_settings_changed)
        camera_form.addRow("虚化强度", self.slider_row(self.blur_slider, self.blur_value))

        self.bokeh_slider, self.bokeh_value = self.make_slider(0, 100, 35, editable=True)
        self.bokeh_slider.valueChanged.connect(self.on_settings_changed)
        camera_form.addRow("光斑强度", self.slider_row(self.bokeh_slider, self.bokeh_value))

        self.bokeh_shape = QComboBox()
        self.bokeh_shape.addItem("圆形（圆叶片）", 0)
        for blades in (5, 6, 7, 8, 9):
            self.bokeh_shape.addItem(f"{blades} 边形光圈", blades)
        self.bokeh_shape.setCurrentIndex(3)
        self.bokeh_shape.currentIndexChanged.connect(self.on_settings_changed)
        camera_form.addRow("光斑形状", self.bokeh_shape)

        self.bokeh_density, self.bokeh_density_value = self.make_slider(0, 100, 55, editable=True)
        self.bokeh_density.valueChanged.connect(self.on_settings_changed)
        camera_form.addRow("光斑密度", self.slider_row(self.bokeh_density, self.bokeh_density_value))

        self.bokeh_size, self.bokeh_size_value = self.make_slider(30, 200, 100, editable=True, suffix="%")
        self.bokeh_size.valueChanged.connect(self.on_settings_changed)
        camera_form.addRow("光斑大小", self.slider_row(self.bokeh_size, self.bokeh_size_value))
        self.full_luminance_bokeh = QCheckBox("高性能光斑效果")
        self.full_luminance_bokeh.setToolTip("按深度和画面位置扩散全亮度光线。光斑大小调节扩散面积，光斑密度调节弱反光的识别灵敏度。")
        self._parked_bokeh_values = {}
        self.full_luminance_bokeh.toggled.connect(self._on_bokeh_mode_changed)
        camera_form.addRow(self.full_luminance_bokeh)

        self.spherical_slider, self.spherical_value = self.make_slider(-100, 100, 0, editable=True)
        self.spherical_slider.valueChanged.connect(self.on_settings_changed)
        self.spherical_slider.setToolTip(
            "球差改变光斑中心和边缘的亮度分布。启用高性能光斑后，"
            "焦点前后的球差表现会有所不同；0 表示不额外加入球差。"
        )
        camera_form.addRow("球差", self.slider_row(self.spherical_slider, self.spherical_value))

        self.cat_eye_slider, self.cat_eye_value = self.make_slider(0, 100, 0, editable=True)
        self.cat_eye_slider.valueChanged.connect(self.on_settings_changed)
        self.cat_eye_slider.setToolTip(
            "猫眼压缩模拟画面边缘的机械暗角，使边缘光斑沿横向压缩。"
            "0 表示不压缩；数值越大，边缘光斑的压缩越明显。"
        )
        camera_form.addRow("猫眼压缩", self.slider_row(self.cat_eye_slider, self.cat_eye_value))
        self.highlight_check = QCheckBox("高光溢出补偿")
        self.highlight_check.setToolTip(
            "启用后抑制超过阈值的高光。高性能光斑会减少额外估算的光源亮度，"
            "普通模式会降低高光光斑的不透明度。"
        )
        self.highlight_check.toggled.connect(self.on_settings_changed)
        camera_form.addRow(self.highlight_check)
        self.highlight_slider, self.highlight_value = self.make_slider(0, 100, 80, editable=True, decimals=1)
        self.highlight_slider.valueChanged.connect(self.on_settings_changed)
        self.highlight_slider.setToolTip(
            "超过阈值后，高光越亮，补偿越强。范围为 0–100，默认值为 80。"
        )
        camera_form.addRow(
            "高光阈值", self.slider_row(self.highlight_slider, self.highlight_value)
        )
        camera_box.setObjectName("CardLens")
        add_card_shadow(camera_box)
        cards_layout.addWidget(camera_box)

        depth_box = QGroupBox("景深与主体")
        depth_form = QFormLayout(depth_box)
        self.depth_mode = QComboBox()
        self.depth_mode.addItems(["深度图对焦（AI 估算 / 导入）", "中心对焦（无需模型）", "手动焦点（点击预览）"])
        self.depth_mode.currentIndexChanged.connect(self.on_depth_mode_changed)
        depth_form.addRow("对焦模式", self.depth_mode)

        self.model_combo = QComboBox()
        self.model_combo.currentIndexChanged.connect(self.on_model_changed)
        depth_form.addRow("AI 模型", self.model_combo)
        self.refresh_model_labels()

        self.import_model_btn = QPushButton("导入 AI 模型")
        self.import_model_btn.setToolTip(
            "选择本地模型文件夹，或填写仓库标识、模型页面链接；本地不存在时将自动下载。"
        )
        self.import_model_btn.clicked.connect(self.import_model)
        depth_form.addRow(self.import_model_btn)

        self.depth_size = QComboBox()
        self.depth_size.addItem("快速 · 518 像素（模型原生）", 518)
        self.depth_size.addItem("标准 · 756 像素", 756)
        self.depth_size.addItem("高 · 1036 像素", 1036)
        self.depth_size.addItem("更高 · 1260 像素", 1260)
        self.depth_size.setToolTip(
            "AI 深度估算的推理分辨率越高，边缘和细节越清晰，处理时间和内存占用也会增加。"
            "更改将在下次运行 AI 估算时生效。"
        )
        self.depth_size.currentIndexChanged.connect(self.on_depth_size_changed)
        depth_form.addRow("深度图分辨率", self.depth_size)

        self.focus_slider, self.focus_value = self.make_slider(0, 100, 45, editable=True, decimals=1)
        self.focus_slider.valueChanged.connect(self.on_settings_changed)
        depth_form.addRow("焦点深度", self.slider_row(self.focus_slider, self.focus_value))

        self.depth_strength, self.depth_strength_value = self.make_slider(0, 100, 70, editable=True)
        self.depth_strength.valueChanged.connect(self.on_settings_changed)
        depth_form.addRow("景深分离", self.slider_row(self.depth_strength, self.depth_strength_value))

        self.ai_btn = QPushButton("运行 AI 深度估算")
        self.ai_btn.clicked.connect(self.run_ai_depth)
        set_variant(self.ai_btn, "accent")
        depth_form.addRow(self.ai_btn)

        self.ai_hint = QLabel("尚未运行，将使用中心对焦")
        self.ai_hint.setWordWrap(True)
        self.ai_hint.setProperty("role", "hint")
        depth_form.addRow(self.ai_hint)

        self.import_depth_btn = QPushButton("导入深度图")
        self.import_depth_btn.clicked.connect(self.import_depth)
        self.export_depth_btn = QPushButton("导出深度图")
        self.export_depth_btn.clicked.connect(self.export_depth)
        depth_io = QWidget()
        depth_io_layout = QHBoxLayout(depth_io)
        depth_io_layout.setContentsMargins(0, 0, 0, 0)
        depth_io_layout.addWidget(self.import_depth_btn)
        depth_io_layout.addWidget(self.export_depth_btn)
        depth_form.addRow(depth_io)

        self.keep_subject = QCheckBox("保护清晰主体")
        self.keep_subject.setChecked(True)
        self.keep_subject.stateChanged.connect(self.on_settings_changed)
        depth_form.addRow(self.keep_subject)

        self.subject_expand_slider, self.subject_expand_value = self.make_slider(-100, 100, 0)
        self.subject_expand_slider.valueChanged.connect(self.on_settings_changed)
        self.subject_expand_slider.setToolTip(
            "主体边缘：负值收缩清晰主体区域，正值扩张主体区域；0 表示自动。"
        )
        self.subject_expand_value.setText("0")
        depth_form.addRow("主体边缘", self.slider_row(self.subject_expand_slider, self.subject_expand_value))

        self.subject_feather_slider, self.subject_feather_value = self.make_slider(0, 100, 0)
        self.subject_feather_slider.valueChanged.connect(self.on_settings_changed)
        self.subject_feather_slider.setToolTip(
            "边缘羽化：柔化主体与背景之间的过渡，用来掩盖深度模型在轮廓处的误差。"
        )
        self.subject_feather_value.setText("0")
        depth_form.addRow("边缘羽化", self.slider_row(self.subject_feather_slider, self.subject_feather_value))
        depth_box.setObjectName("CardDepth")
        add_card_shadow(depth_box)
        cards_layout.addWidget(depth_box)

        output_box = QGroupBox("预览与输出")
        output_form = QFormLayout(output_box)
        interface_actions = QWidget()
        interface_layout = QHBoxLayout(interface_actions)
        interface_layout.setContentsMargins(0, 0, 0, 0)
        interface_layout.addWidget(self.theme_btn)
        interface_layout.addWidget(self.motion_btn)
        output_form.addRow("界面", interface_actions)
        readme_btn = QPushButton("项目说明")
        readme_btn.clicked.connect(self.show_readme)
        output_form.addRow("帮助", readme_btn)
        self.home_btn = QPushButton("返回欢迎页")
        self.home_btn.clicked.connect(self.show_welcome)
        output_form.addRow("导航", self.home_btn)
        self.preview_scale = QComboBox()
        self.preview_scale.addItems(["快速预览（最长边 1000 像素）", "标准预览（最长边 1400 像素）", "高质量预览（原尺寸）"])
        self.preview_scale.currentIndexChanged.connect(self.reload_preview_size)
        output_form.addRow("预览质量", self.preview_scale)
        self.range_check = QCheckBox("显示效果范围（红色标记）")
        self.range_check.setToolTip(
            "仅影响预览：以半透明红色标记虚化或光斑影响的区域，便于检查效果边界；导出文件不受影响。"
        )
        self.range_check.toggled.connect(self.refresh_display)
        output_form.addRow(self.range_check)
        self.report_btn = QPushButton("导出使用报告")
        self.report_btn.setToolTip(
            "将运行日志与设备信息（CPU、内存、显卡及驱动等）导出为文本文件，供问题排查使用。\n"
            f"当前计算后端：{self.engine.backend_description}"
        )
        self.report_btn.clicked.connect(self.export_usage_report)
        output_form.addRow("使用报告", self.report_btn)
        self.version_label = QLabel(f"v{APP_VERSION} · {APP_EDITION}")
        self.version_label.setProperty("role", "status")
        output_form.addRow("版本", self.version_label)
        output_box.setObjectName("CardOutput")
        add_card_shadow(output_box)
        cards_layout.addWidget(output_box)

        hist_box = QGroupBox("直方图")
        hist_layout = QVBoxLayout(hist_box)
        self.histogram = HistogramPanel(motion=MOTION, animation_factory=SmoothPropertyAnimation)
        hist_layout.addWidget(self.histogram)
        hist_box.setObjectName("CardHistogram")
        add_card_shadow(hist_box)
        cards_layout.addWidget(hist_box)

        log_box = QGroupBox("操作记录（Ctrl+Z 撤销 / Ctrl+Y 恢复）")
        log_layout = QVBoxLayout(log_box)
        self.log_list = QListWidget()
        self.log_list.setObjectName("ActionLog")
        self.log_list.setMaximumHeight(150)
        self.log_list.itemClicked.connect(self.on_log_clicked)
        log_layout.addWidget(self.log_list)
        log_buttons = QHBoxLayout()
        self.undo_btn = QPushButton("撤销 Ctrl+Z")
        self.undo_btn.clicked.connect(self.undo)
        self.redo_btn = QPushButton("恢复 Ctrl+Y")
        self.redo_btn.clicked.connect(self.redo)
        log_buttons.addWidget(self.undo_btn)
        log_buttons.addWidget(self.redo_btn)
        log_layout.addLayout(log_buttons)
        log_box.setObjectName("CardHistory")
        add_card_shadow(log_box)
        cards_layout.addWidget(log_box)
        controls_layout.addStretch()

        note = QLabel("可导入深度图或使用中心对焦。AI 模型未在本地安装时，首次运行需要联网下载。")
        note.setWordWrap(True)
        note.setProperty("role", "hint")
        controls_layout.addWidget(note)

        # Long combo entries (focus mode / model / preview names) would
        # otherwise force the form wider than the column and clip the label
        # column, so their minimum width is pinned to a few characters.
        for combo in controls.findChildren(QComboBox):
            combo.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
            )
            combo.setMinimumContentsLength(8)

        # Motion.Lab 的点击波纹：只在操作时出现，不影响预览图像。
        interactive_controls = (controls.findChildren(QSlider) +
                                controls.findChildren(QComboBox) +
                                controls.findChildren(QCheckBox))
        self._control_ripples = [ButtonRipple(widget) for widget in interactive_controls]
        self._card_tilts = [CardTilt(card) for card in
                            (camera_box, depth_box, output_box, hist_box, log_box)]
        self._update_glow()

        # The control column keeps growing as options are added, so it lives in
        # a scroll area: it never gets cut off on a short window/screen.
        scroll = QScrollArea()
        scroll.setObjectName("ControlsScroll")
        scroll.setWidget(controls)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setMinimumWidth(300)
        scroll.setMaximumWidth(400)
        self._dark_card_scroll=install_card_scroll(scroll,MOTION)
        self.card_reorder = CardDragReorder(cards_layout, scroll, self)
        for card in (camera_box, depth_box, output_box, hist_box, log_box):
            self.card_reorder.watch(card)
        self.card_reorder.orderChanged.connect(self._save_card_order)
        self._floating_panels = [
            FloatingPanelController(original_panel, canvases, "原图", self),
            FloatingPanelController(effect_panel, right_side, "效果图", self),
            FloatingPanelController(depth_panel, right_side, "深度图", self),
        ]
        self._floating_panels.extend(
            FloatingPanelController(card, cards_layout, card.title(), self, self.card_reorder)
            for card in (camera_box, depth_box, output_box, hist_box, log_box)
        )
        side_panel = QWidget()
        side_panel.setMinimumWidth(300)
        side_panel.setMaximumWidth(400)
        self.side_panel_layout = QVBoxLayout(side_panel)
        self.side_panel_layout.setContentsMargins(0, 0, 0, 0)
        self.side_panel_layout.setSpacing(8)
        self.side_panel_layout.addWidget(scroll, 1)
        splitter.addWidget(side_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([940, 320])
        self._update_editorial_geometry()

        self.setStatusBar(QStatusBar())
        self.statusBar().addPermanentWidget(self.status_label)
        self.status_label.setVisible(self._theme_mode == "dark")
        # 启动时把焦点留给窗口：否则 Qt 会自动高亮第一个按钮，
        # "导入图片"一开机就是焦点白线，看起来像被选中。
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFocus()
        self._refresh_action_roles()

        open_action = QAction("打开图片", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.open_image)
        self.addAction(open_action)
        save_action = QAction("导出", self)
        save_action.setShortcut("Ctrl+Shift+S")
        save_action.triggered.connect(self.export_image)
        self.addAction(save_action)

        # -- 操作记录 / 撤销重做（本次更新主题："快捷键革命"） ----------------
        self.history = ActionHistory()
        self._history_ready = False
        self._suppress_history = False
        self._pending_label = "调整参数"
        self._history_timer = QTimer(self)
        self._history_timer.setSingleShot(True)
        self._history_timer.setInterval(400)
        self._history_timer.timeout.connect(self._commit_history)
        self._control_labels = {
            self.aperture: "调整 模拟光圈",
            self.blur_slider: "调整 虚化强度",
            self.bokeh_slider: "调整 光斑强度",
            self.bokeh_shape: "调整 光斑形状",
            self.full_luminance_bokeh: "切换 高性能光斑",
            self.bokeh_density: "调整 光斑密度",
            self.bokeh_size: "调整 光斑大小",
            self.spherical_slider: "调整 球差",
            self.cat_eye_slider: "调整 猫眼压缩",
            self.highlight_check: "切换 高光溢出补偿",
            self.highlight_slider: "调整 高光阈值",
            self.focus_slider: "调整 焦点深度",
            self.depth_strength: "调整 景深分离",
            self.keep_subject: "切换 保护主体",
            self.subject_expand_slider: "调整 主体边缘",
            self.subject_feather_slider: "调整 主体羽化",
            self.preview_scale: "切换 预览质量",
            self.depth_mode: "切换 对焦模式",
            self.depth_size: "切换 深度图分辨率",
            self.model_combo: "切换 AI 模型",
        }

        undo_action = QAction("撤销", self)
        undo_action.setShortcut("Ctrl+Z")
        undo_action.triggered.connect(self.undo)
        self.addAction(undo_action)
        redo_action = QAction("恢复", self)
        redo_action.setShortcuts(["Ctrl+Y", "Ctrl+Shift+Z"])
        redo_action.triggered.connect(self.redo)
        self.addAction(redo_action)

        # Restore the previous session's settings once every widget exists.
        self._load_config()
        self._history_ready = True
        self.history.reset("初始状态", self._snapshot(), self._signature())
        self._refresh_log()
        self.light_workspace = WorkspacePage(self, asset("workspace"), MOTION)
        self.pages.addWidget(self.light_workspace)
        self.statusBar().messageChanged.connect(self.light_workspace.set_status)
        self.model_combo.currentIndexChanged.connect(lambda _index:self._record("切换 AI 模型"))
        self.model_combo.currentIndexChanged.connect(self._sync_light_workspace)
        self._card_tilts.extend(CardTilt(card) for card in
                                (self.light_workspace.history_panel,self.light_workspace.parameters_panel))
        for tilt in self._card_tilts[-2:]:
            tilt.effect.setBlurRadius(0)
            tilt.effect.setOffset(0,0)
            tilt.effect.setColor(QColor(0,0,0,0))
        self._control_ripples.extend(ButtonRipple(widget) for widget in
                                    self.light_workspace.findChildren(QPushButton))
        self._update_glow()
        self.welcome_page = WelcomePage(asset("welcome"), MOTION, self.pages)
        self.welcome_page.actionRequested.connect(self._enter_workspace)
        self.pages.addWidget(self.welcome_page)
        self.show_welcome()
        self.window_frame = FramelessWindowFrame(self)
        # 设备档案延迟到窗口出现后再采集（要查显卡/驱动，别拖慢启动）。
        QTimer.singleShot(1200, self.log_device_profile)

    def active_workspace(self):
        return self.light_workspace if self._theme_mode == "light" else self.editorial_background

    def is_workspace_active(self):
        current = self.pages.currentWidget()
        return current is self.editorial_background or current is getattr(self,"light_workspace",None)

    def _sync_light_workspace(self):
        if hasattr(self,"light_workspace"):
            self.light_workspace.sync()

    def _session_snapshot(self):
        state = self._snapshot()
        state["model_ref"] = self.model_combo.currentData()
        state["view_range"] = self.range_check.isChecked()
        state["view_compare"] = self.compare_btn.isChecked()
        return state

    def _save_session(self):
        if self.original_full is None or not self.image_path:
            return
        try:
            save_session(settings_path().parent / "sessions", self._session_snapshot())
        except (OSError, ValueError) as exc:
            self.event_log.log("session.save.error", error=str(exc))
            QMessageBox.warning(self, "会话保存失败",
                                "当前工作未能保存到会话文件，原图未受影响。\n"
                                "如需保留当前效果，请导出图片。\n\n" + str(exc))

    def show_welcome(self):
        if not hasattr(self, "welcome_page"):
            return
        if self._export_waiters:
            self.statusBar().showMessage("请在导出完成后返回欢迎页。", 4000)
            return
        if self._workspace_entered and self.is_workspace_active():
            self._history_timer.stop()
            self._commit_history()
            self._resume_state = self._session_snapshot()
            self._resume_history = self.history
            self._save_session()
            self._save_config()
            self._ai_generation += 1
            if self.depth_worker is not None:
                self.depth_worker.requestInterruption()
        self._render_timer.stop()
        self._invalidate_preview()
        self._title_timer.stop()
        for canvas in (self.before_canvas, self.after_canvas):
            canvas._flock_timer.stop()
        for controller in self._floating_panels:
            controller.restore()
        for controller in self.light_workspace.floating:
            controller.restore()
        if not self._welcome_action_states:
            self._welcome_action_states = [(action, action.isEnabled()) for action in self.actions()]
            for action, _enabled in self._welcome_action_states:
                action.setEnabled(False)
        self.pages.setCurrentWidget(self.welcome_page)
        self.statusBar().hide()
        self.welcome_page.present()
        self.event_log.log("navigation.welcome")

    def _enter_workspace(self, action):
        state, warning, history = None, "", None
        if action == "next":
            if self._resume_state is not None:
                state, history = dict(self._resume_state), self._resume_history
            else:
                result = load_session(settings_path().parent / "sessions")
                state, warning = result.state, result.warning
                if state is not None and state.get("image_path"):
                    try:
                        signature = source_signature(state["image_path"])
                        if state.get("image_source_signature") != signature:
                            state["depth"] = state["depth_full"] = state["depth_name"] = None
                            warning = "上一会话的原图已更新，旧深度图未载入。"
                        state["original_full"] = load_image_rgb(state["image_path"])
                        if source_signature(state["image_path"]) != signature:
                            signature = None
                            state["depth"] = state["depth_full"] = state["depth_name"] = None
                            warning = "读取期间原图发生变化，旧深度图未载入。"
                        state["image_source_signature"] = signature
                    except Exception as exc:
                        for key in ("image_path", "original_full", "depth", "depth_full", "depth_name"):
                            state[key] = None
                        warning = f"无法读取上次图片，已恢复参数：{exc}"
                if state is None and not warning:
                    warning = "暂无已保存的图片会话，已沿用上次参数进入工作区。"
        if state is None:
            state = self._snapshot()
            for key in ("image_path", "original", "original_full", "depth", "depth_full", "depth_name"):
                state[key] = None
            state["image_source_signature"] = None
            state["view_range"] = state["view_compare"] = False
        model_ref = state.get("model_ref")
        if model_ref:
            index = self.model_combo.findData(model_ref)
            if index >= 0:
                state["model_combo"] = index
        self._history_timer.stop()
        self._suppress_history = True
        try:
            self._restore(state)
            self.range_check.setChecked(bool(state.get("view_range", False)))
            self.compare_btn.setChecked(bool(state.get("view_compare", False)))
        finally:
            self._suppress_history = False
        self.update_histogram()
        if self.original_full is None:
            self.ai_hint.setText("尚未运行，将使用中心对焦")
        self.history = history if history is not None else ActionHistory()
        if history is None:
            self.history.reset("恢复上次工作" if action == "next" else "初始状态",
                               self._snapshot(), self._signature())
        self._refresh_log()
        self._workspace_entered = True
        self.pages.setCurrentWidget(self.active_workspace())
        self.statusBar().setVisible(self._theme_mode == "dark")
        self._sync_light_workspace()
        for item, enabled in self._welcome_action_states:
            item.setEnabled(enabled)
        self._welcome_action_states = []
        self._update_editorial_geometry()
        self._apply_motion_settings()
        self._animate_title()
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        if warning:
            self.statusBar().showMessage(warning, 10000)
        self.event_log.log("navigation.workspace", action=action, restored_image=self.image_path)
        QTimer.singleShot(1200, self.log_device_profile)

    def _animate_title(self):
        if not MOTION["enabled"] or not MOTION["title"]:
            self._title_timer.stop()
            self.title_label.setText(BRAND_TITLE)
            return
        self._title_index = 0
        self.title_label.setText("│")
        self._title_timer.setInterval(max(30, int(150 / MOTION["speed"])))
        self._title_timer.start()

    def _type_title_step(self):
        self._title_index += 1
        if self._title_index >= len(BRAND_TITLE):
            self._title_timer.stop()
            self.title_label.setText(BRAND_TITLE)
        else:
            self.title_label.setText(BRAND_TITLE[:self._title_index] + "│")

    def _update_theme_button(self):
        dark = self._theme_mode == "dark"
        self.theme_btn.setText("浅色模式" if dark else "黑夜模式")
        self.theme_btn.setToolTip("切换界面主题")

    def show_motion_settings(self):
        dialog = DesignDialog(self)
        dialog.setWindowTitle("动画设置")
        dialog.resize(520, 740)
        dialog.setMinimumWidth(560)
        layout = QVBoxLayout(dialog.body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        subtitle = QLabel("按部件调整动态效果、速度与性能。")
        subtitle.setProperty("role", "hint")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        scroll = QScrollArea(dialog)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        sections = QVBoxLayout(content)
        sections.setContentsMargins(0, 0, 4, 0)
        sections.setSpacing(10)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        checks = {}
        combos = {}
        def section(title):
            box = QGroupBox(title)
            form = QFormLayout(box)
            form.setHorizontalSpacing(16)
            form.setVerticalSpacing(8)
            sections.addWidget(box)
            return form

        def check(form, key, label):
            widget = QCheckBox(label)
            widget.setObjectName("motion_" + key)
            widget.setChecked(MOTION[key])
            checks[key] = widget
            form.addRow(widget)

        def choice(form, key, label, entries):
            combo = QComboBox()
            combo.setObjectName("motion_" + key)
            for text, value in entries:
                combo.addItem(text, value)
            combo.setCurrentIndex(combo.findData(MOTION[key]))
            combos[key] = combo
            form.addRow(label, combo)

        general = section("全局 · 运行与性能")
        check(general, "enabled", "启用界面动画")
        choice(general, "speed", "界面速度", (("慢 · 0.5×", .5), ("标准 · 1×", 1.), ("快 · 1.5×", 1.5), ("更快 · 2×", 2.)))
        choice(general, "interval", "性能档位", (("流畅 · 目标 125 次/秒", 8), ("均衡 · 目标 60 次/秒", 16), ("省电 · 目标 30 次/秒", 33)))
        hint = QLabel("目标更新频率受设备和显示器影响。")
        hint.setProperty("role", "hint")
        hint.setWordWrap(True)
        general.addRow(hint)

        title = section("标题")
        check(title, "title", "启动和切换主题时逐字显示")
        cards = section("控制卡片")
        check(cards, "tilt", "鼠标悬停时倾斜")
        choice(cards, "tilt_strength", "倾斜幅度", (("轻柔", .5), ("标准", 1.), ("明显", 1.5)))
        flock = section("空画布 · 鸟群")
        check(flock, "flock", "显示鸟群并跟随鼠标")
        check(flock, "flock_varied_speed", "粒子使用不同速度")
        choice(flock, "flock_speed", "飞行速度", (("慢 · 0.5×", .5), ("标准 · 1×", 1.), ("快 · 1.5×", 1.5), ("更快 · 2×", 2.)))
        choice(flock, "flock_response", "跟随灵敏度", (("柔和", .5), ("标准", 1.), ("灵敏", 1.5)))
        choice(flock, "particles", "鸟群数量", (("少 · 12", 12), ("标准 · 24", 24), ("多 · 40", 40)))
        sections.addStretch()

        def update_enabled():
            enabled = checks["enabled"].isChecked()
            for key in ("title", "tilt", "flock"):
                checks[key].setEnabled(enabled)
            checks["flock_varied_speed"].setEnabled(enabled and checks["flock"].isChecked())
            combos["speed"].setEnabled(enabled)
            combos["interval"].setEnabled(enabled)
            combos["tilt_strength"].setEnabled(enabled and checks["tilt"].isChecked())
            for key in ("flock_speed", "flock_response", "particles"):
                combos[key].setEnabled(enabled and checks["flock"].isChecked())
        for key in ("enabled", "tilt", "flock"):
            checks[key].toggled.connect(update_enabled)
        update_enabled()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("保存设置")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        set_variant(buttons.button(QDialogButtonBox.StandardButton.Ok), "primary")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        dialog.set_footer(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        MOTION.update({key: check.isChecked() for key, check in checks.items()})
        MOTION.update({key: combo.currentData() for key, combo in combos.items()})
        store = self._store()
        for key, value in MOTION.items():
            store.setValue("motion/" + key, value)
        store.sync()
        self._apply_motion_settings()

    def _apply_motion_settings(self):
        for area in self.findChildren(QScrollArea):
            controller=getattr(area,'_card_scroll',None)
            if controller is not None:controller.refresh()
        self._title_timer.stop()
        self.title_label.setText(BRAND_TITLE)
        for tilt in self._card_tilts:
            if not MOTION["enabled"] or not MOTION["tilt"]:
                tilt.animation.stop()
                tilt._set_offset(QPointF())
        for canvas in (self.before_canvas, self.after_canvas):
            canvas._flock_timer.stop()
            canvas._flock_timer.setInterval(MOTION["interval"])
            count = MOTION["particles"]
            canvas._flock = canvas._flock[:count]
            while len(canvas._flock) < count:
                canvas._flock.append([random.random(), random.random(), random.uniform(-1, 1),
                                      random.uniform(-1, 1), random.uniform(.65, 1.35),
                                      random.randint(140, 255)])
            canvas._flock_last_tick = time.monotonic()
            if (MOTION["enabled"] and MOTION["flock"] and canvas._pixmap is None
                    and self.pages.currentWidget() is self.editorial_background):
                canvas._flock_timer.start()
            canvas.update()
        if hasattr(self,"light_workspace"):
            self.light_workspace.sync_motion()

    def show_readme(self):
        root = Path(getattr(sys, "_MEIPASS", _app_root())) if getattr(sys, "frozen", False) else _app_root()
        readme = root / "README.md"
        if not readme.is_file():
            QMessageBox.warning(self, "无法打开项目说明", f"找不到项目说明文件。\n{readme}")
            return
        dialog = DesignDialog(self)
        dialog.setWindowTitle("项目说明")
        dialog.resize(800, 700)
        layout = QVBoxLayout(dialog.body)
        layout.setContentsMargins(0,0,0,0)
        browser = ProjectHelpBrowser(dialog)
        browser.setSource(QUrl.fromLocalFile(str(readme)))
        if browser.document().isEmpty():
            dialog.deleteLater()
            QMessageBox.warning(self, "无法打开项目说明", f"无法读取项目说明文件。\n{readme}")
            return
        browser.unavailable.connect(lambda message: QMessageBox.information(dialog, "项目资料", message))
        layout.addWidget(browser)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, dialog)
        back = close.addButton("上一页", QDialogButtonBox.ButtonRole.ActionRole)
        back.hide()
        back.clicked.connect(browser.backward)
        browser.backwardAvailable.connect(back.setVisible)
        close.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        close.rejected.connect(dialog.reject)
        dialog.set_footer(close)
        dialog.exec()
        dialog.deleteLater()

    def _update_glow(self):
        for lift in self._hover_lift:
            color = QColor("#F6D05D" if self._theme_mode == "dark" else "#D92D20")
            color.setAlpha(140)
            lift.effect.setColor(color)
        for ripple in self._ripples[:2]:
            ripple.color = QColor("#141414" if self._theme_mode == "dark" else "#FFFFFF")
        self._ripples[2].color = QColor("#F6D05D" if self._theme_mode == "dark" else "#141414")
        for ripple in getattr(self, "_control_ripples", ()):
            ripple.color = QColor("#F6D05D" if self._theme_mode == "dark" else "#D92D20")
            if hasattr(self,"light_workspace") and self.light_workspace.isAncestorOf(ripple.parent()):
                ripple.color = QColor("#001EFF")

    def toggle_theme(self):
        if getattr(self, "_theme_transition", None) is not None:
            return
        # Freeze the old appearance while the real widgets change underneath.
        snapshot = self.grab()
        overlay = QWidget(self)
        overlay.setGeometry(self.rect())
        overlay.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        image = QLabel(overlay)
        image.setGeometry(overlay.rect())
        image.setPixmap(snapshot)
        image.setScaledContents(True)
        blur = QGraphicsBlurEffect(image)
        blur.setBlurRadius(0)
        image.setGraphicsEffect(blur)
        opacity = QGraphicsOpacityEffect(overlay)
        overlay.setGraphicsEffect(opacity)
        overlay.show()
        overlay.raise_()
        self._theme_transition = overlay

        animation = SmoothVariantAnimation(overlay)
        animation.setDuration(180)
        animation.setStartValue(0.0)
        animation.setEndValue(12.0)
        animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        animation.valueChanged.connect(blur.setBlurRadius)
        animation.finished.connect(lambda: self._finish_theme_change(overlay, opacity))
        animation.start()

    def _finish_theme_change(self, overlay, opacity):
        was_workspace = self.is_workspace_active()
        for controller in self._floating_panels:
            controller.restore()
        if hasattr(self,"light_workspace"):
            for controller in self.light_workspace.floating: controller.restore()
        self._theme_mode = "light" if self._theme_mode == "dark" else "dark"
        apply_theme(QApplication.instance(), self._theme_mode)
        self.editorial_background.dark = self._theme_mode == "dark"
        self.editorial_background.update()
        self._update_editorial_geometry()
        self.status_label.setVisible(self._theme_mode == "dark")
        self._update_theme_button()
        if was_workspace:
            self.pages.setCurrentWidget(self.active_workspace())
            self.statusBar().setVisible(self._theme_mode == "dark")
        self._sync_light_workspace()
        self._apply_motion_settings()
        self._update_glow()
        store = self._store()
        store.setValue("theme", self._theme_mode)
        store.sync()
        fade = SmoothVariantAnimation(overlay)
        fade.setDuration(220)
        fade.setStartValue(1.0)
        fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        fade.valueChanged.connect(opacity.setOpacity)
        fade.finished.connect(lambda: self._end_theme_transition(overlay))
        fade.start()

    def _end_theme_transition(self, overlay):
        self._theme_transition = None
        overlay.deleteLater()
        self._animate_title()

    def resizeEvent(self, event):
        overlay = getattr(self, "_theme_transition", None)
        if overlay is not None:
            overlay.setGeometry(self.rect())
            for label in overlay.findChildren(QLabel):
                label.setGeometry(overlay.rect())
        self._update_editorial_geometry()
        super().resizeEvent(event)

    def _update_editorial_geometry(self):
        outer = getattr(self, "_editorial_outer", None)
        if outer is None:
            return
        if hasattr(self, "light_workspace") and self._theme_mode == "light":
            if self.pages.currentWidget() is self.light_workspace: self.light_workspace.relayout()
            return
        if hasattr(self, "welcome_page") and self.pages.currentWidget() is self.welcome_page:
            return
        if self._theme_mode == "light":
            width, height = self.width(), self.height()
            outer.setContentsMargins(round(width * .072), round(height * .103),
                                     round(width * .025), round(height * .035))
            self._main_splitter.setHandleWidth(max(14, round(width * .022)))
            if hasattr(self, "editorial_plate"):
                plate_width = max(230, round(width * .285))
                self.editorial_plate.setFixedWidth(plate_width)
                if hasattr(self, "brand_gap"):
                    self.brand_gap.setFixedWidth(max(0, plate_width - round(width * (.072 - .018))))
                if hasattr(self, "brand_overlay"):
                    brand_height = self.brand_overlay.sizeHint().height()
                    self.brand_overlay.setGeometry(round(width * .018),
                        max(0, self.centralWidget().height() - round(height * .035) - brand_height),
                        plate_width, brand_height)
                    self.brand_overlay.raise_()
        else:
            outer.setContentsMargins(56, 72, 52, 16)
            self._main_splitter.setHandleWidth(8)
            if hasattr(self, "editorial_plate"):
                self.editorial_plate.setFixedWidth(220)
                if hasattr(self, "brand_gap"):
                    self.brand_gap.setFixedWidth(220)
                if hasattr(self, "brand_overlay"):
                    brand_height = self.brand_overlay.sizeHint().height()
                    self.brand_overlay.setGeometry(56, max(0, self.centralWidget().height() - 16 - brand_height),
                                                   220, brand_height)
                    self.brand_overlay.raise_()
        buttons = getattr(self, "_overlay_buttons", None)
        if buttons is not None:
            width, height = self.width(), self.height()
            if self._theme_mode == "light":
                button_width = max(150, round(width * .122))
                button_height = max(48, round(height * .082))
                left = max(round(width * .316), self.brand_overlay.x() + self.brand_overlay.width() + 10)
                gap = max(8, round(width * .012))
                right = round(width * .021)
                bottom = round(height * .035)
            else:
                button_width, button_height = 150, 50
                left = self.brand_overlay.x() + self.brand_overlay.width() + 10
                gap, right, bottom = 10, 52, 16
            self.brand_gap.setFixedHeight(button_height)
            y = max(0, self.centralWidget().height() - bottom - button_height)
            for button, x in zip(buttons, (left, left + button_width + gap, width - right - button_width)):
                button.setGeometry(x, y, button_width, button_height)
                button.raise_()
            transition = getattr(self, "_theme_transition", None)
            if transition is not None:
                transition.raise_()

    def _refresh_action_roles(self):
        """空状态禁用导出；有图后启用。按钮角色由 variant 固定，不随状态变。"""
        self.export_btn.setEnabled(self.original_full is not None)
        self.compare_btn.setEnabled(self.original_full is not None)
        empty = "true" if self.original_full is None else "false"
        for canvas in (self.before_canvas, self.after_canvas, self.depth_preview):
            if canvas.property("empty") != empty:
                canvas.setProperty("empty", empty)
                canvas.style().unpolish(canvas)
                canvas.style().polish(canvas)
                canvas.update()
        self._sync_light_workspace()

    # -- 操作记录 / 撤销重做（Ctrl+Z / Ctrl+Y） ----------------------------

    def _control_label(self):
        return self._control_labels.get(self.sender(), "调整参数")

    def _snapshot(self):
        """记录一份完整状态：控件取值 + 数据引用（不复制像素）。"""
        state = {}
        for name, kind in _HISTORY_WIDGETS:
            widget = getattr(self, name)
            if kind == "index":
                state[name] = widget.currentIndex()
            elif kind == "checked":
                state[name] = widget.isChecked()
            else:
                state[name] = widget.value()
        state["parked_bokeh_values"] = dict(self._parked_bokeh_values)
        state["protect_subject"] = self.keep_subject.isChecked()
        state["image_path"] = self.image_path
        state["image_source_signature"] = self._image_source_signature
        state["original"] = self.original
        state["original_full"] = self.original_full
        state["depth"] = self.depth
        state["depth_full"] = self._depth_full
        state["depth_name"] = self._depth_name
        return state

    def _signature(self):
        """轻量指纹，用来判断"状态是不是真变了"。"""
        parts = []
        for name, kind in _HISTORY_WIDGETS:
            widget = getattr(self, name)
            if kind == "index":
                parts.append(widget.currentIndex())
            elif kind == "checked":
                parts.append(widget.isChecked())
            else:
                parts.append(widget.value())
        parts.append(tuple(sorted(self._parked_bokeh_values.items())))
        parts.append(self.keep_subject.isChecked())
        parts.append(self.image_path)
        parts.append(id(self.original_full))
        parts.append(id(self._depth_full))
        parts.append(id(self.depth))
        parts.append(self._depth_name)
        return tuple(parts)

    def _record(self, label, immediate=False):
        """记一次操作；滑块拖动由 400ms 定时器合并为"一次"操作。"""
        if not self._history_ready or self._suppress_history:
            return
        self._pending_label = label
        if immediate:
            self._commit_history()
        else:
            self._history_timer.start()

    def _commit_history(self):
        if not self._history_ready or self._suppress_history:
            return
        if self.history.push(self._pending_label, self._snapshot(), self._signature()):
            self.event_log.log(
                "ui.action", action=self._pending_label, params=asdict(self.settings())
            )
            self._refresh_log()

    def _refresh_log(self):
        self.log_list.blockSignals(True)
        self.log_list.clear()
        for position, label in enumerate(self.history.labels(), start=1):
            self.log_list.addItem(f"{position}. {label}")
        if self.history.index >= 0:
            self.log_list.setCurrentRow(self.history.index)
        self.log_list.blockSignals(False)
        self.undo_btn.setEnabled(self.history.can_undo)
        self.redo_btn.setEnabled(self.history.can_redo)
        self._sync_light_workspace()

    def _apply_history(self, label, state):
        self._suppress_history = True
        try:
            self._restore(state)
        finally:
            self._suppress_history = False
        self.statusBar().showMessage(f"已回到：{label}", 3000)
        self._refresh_log()

    def undo(self):
        entry = self.history.undo()
        if entry is None:
            self.statusBar().showMessage("没有可撤销的操作", 3000)
            return
        self.event_log.log("history.undo", label=entry[0])
        self._apply_history(*entry)

    def redo(self):
        entry = self.history.redo()
        if entry is None:
            self.statusBar().showMessage("没有可恢复的操作", 3000)
            return
        self.event_log.log("history.redo", label=entry[0])
        self._apply_history(*entry)

    def on_log_clicked(self, item):
        """点日志列表里的某一步，直接跳回那一步的状态。"""
        row = self.log_list.row(item)
        if row == self.history.index:
            return
        entry = self.history.goto(row)
        if entry is not None:
            self._apply_history(*entry)

    def _restore(self, state):
        self._parked_bokeh_values = dict(state.get("parked_bokeh_values", {}))
        self._set_bokeh_ranges(bool(state.get("full_luminance_bokeh", False)), remember=False)
        for name, kind in _HISTORY_WIDGETS:
            widget = getattr(self, name)
            value = state.get(name)
            widget.blockSignals(True)
            if value is not None:
                if kind == "index":
                    if 0 <= value < widget.count():
                        widget.setCurrentIndex(value)
                elif kind == "checked":
                    widget.setChecked(bool(value))
                else:
                    widget.setValue(value)
            widget.blockSignals(False)
        self.keep_subject.blockSignals(True)
        self.keep_subject.setChecked(bool(state.get("protect_subject", True)))
        self.keep_subject.blockSignals(False)

        self.image_path = state.get("image_path")
        self._image_source_signature = state.get("image_source_signature")
        self.original_full = state.get("original_full")
        self._depth_full = state.get("depth_full")
        self._depth_name = state.get("depth_name")
        self.depth = state.get("depth")
        self._ai_generation += 1
        self.ai_btn.setText("运行 AI 深度估算")

        if self.original_full is None:
            self._invalidate_preview()
            self._render_timer.stop()
            self.original = None
            self.processed = None
            self.depth = None
            self.before_canvas.set_image(None)
            self.after_canvas.set_image(None)
            self.depth_preview.set_depth(None)
            self.setWindowTitle(f"{APP_TITLE}  v{APP_VERSION} · {APP_EDITION}")
            self.status_label.setText(self.engine.backend_description)
        else:
            # 先按当前预览质量重建预览图与深度，再补回记录里的那份深度。
            self.reload_preview_size()
            recorded_depth = state.get("depth")
            if (recorded_depth is not None and self.original is not None
                    and recorded_depth.shape[:2] == self.original.shape[:2]):
                self.depth = recorded_depth
                self.depth_preview.set_depth(self.depth)
                self.ai_hint.setText("已按操作记录恢复深度图。")
            self.setWindowTitle(
                f"{APP_TITLE}  v{APP_VERSION} · {APP_EDITION} · {Path(self.image_path).name}"
            )
        self._refresh_action_roles()
        self.update_histogram()
        self.on_settings_changed()
        self._sync_light_workspace()

    # -- 配置记忆：下次打开继承上次设置 ------------------------------------

    def _store(self):
        return QSettings(str(settings_path()), QSettings.Format.IniFormat)

    def _save_card_order(self, names):
        """拖动排序后立刻写盘，下次启动沿用同一顺序。"""
        store = self._store()
        store.setValue("card_order", ",".join(names))
        store.sync()

    @staticmethod
    def _as_bool(value, default):
        if value is None:
            return default
        return str(value).strip().lower() in ("1", "true", "yes")

    def _load_config(self):
        """Restore the last-used settings so a fresh launch inherits them."""
        store = self._store()

        def as_int(key, default):
            try:
                return int(store.value(key))
            except (TypeError, ValueError):
                return default

        expanded = self._as_bool(store.value("full_luminance_bokeh"), False)
        parked = store.value("parked_bokeh_values")
        if isinstance(parked, dict):
            for name in (*_BOKEH_RANGES, "aperture", "bokeh_shape"):
                try:
                    self._parked_bokeh_values[name] = int(parked[name])
                except (KeyError, TypeError, ValueError):
                    pass
        self.full_luminance_bokeh.blockSignals(True)
        self.full_luminance_bokeh.setChecked(expanded)
        self.full_luminance_bokeh.blockSignals(False)
        self._set_bokeh_ranges(expanded, remember=False)
        index = as_int("aperture", self.aperture.currentIndex())
        if 0 <= index < self.aperture.count():
            self.aperture.setCurrentIndex(index)
        self.blur_slider.setValue(as_int("blur", self.blur_slider.value()))
        self.bokeh_slider.setValue(as_int("bokeh", self.bokeh_slider.value()))
        index = as_int("bokeh_shape", self.bokeh_shape.currentIndex())
        if 0 <= index < self.bokeh_shape.count():
            self.bokeh_shape.setCurrentIndex(index)
        self.bokeh_density.setValue(as_int("bokeh_density", self.bokeh_density.value()))
        self.bokeh_size.setValue(as_int("bokeh_size", self.bokeh_size.value()))
        self.spherical_slider.setValue(
            as_int("spherical", self.spherical_slider.value())
        )
        self.cat_eye_slider.setValue(as_int("cat_eye", self.cat_eye_slider.value()))
        self.focus_slider.setValue(as_int("focus_tenths", as_int("focus", 45) * _FOCUS_VALUE_SCALE))
        self.depth_strength.setValue(
            as_int("depth_strength", self.depth_strength.value())
        )
        self.keep_subject.setChecked(self._as_bool(store.value("protect_subject"), True))
        self.subject_expand_slider.setValue(
            as_int("subject_expand", self.subject_expand_slider.value())
        )
        self.subject_feather_slider.setValue(
            as_int("subject_feather", self.subject_feather_slider.value())
        )
        self.highlight_check.setChecked(
            self._as_bool(store.value("highlight_rolloff"), False)
        )
        self.highlight_slider.setValue(
            as_int("highlight_threshold_tenths", as_int("highlight_threshold", 80) * _FOCUS_VALUE_SCALE)
        )
        self.preview_scale.setCurrentIndex(
            as_int("preview_scale", self.preview_scale.currentIndex())
        )
        self.depth_mode.setCurrentIndex(
            as_int("depth_mode", self.depth_mode.currentIndex())
        )
        size_index = as_int("depth_size_index", self.depth_size.currentIndex())
        if 0 <= size_index < self.depth_size.count():
            self.depth_size.setCurrentIndex(size_index)
        model = store.value("model")
        if model:
            index = self.model_combo.findData(str(model))
            if index >= 0:
                self.model_combo.setCurrentIndex(index)
        geometry = store.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        order = str(store.value("card_order") or "")
        if order:
            self.card_reorder.apply_order(order.split(","))
        self.on_settings_changed()

    def _save_config(self):
        """Persist the current settings (called on close)."""
        store = self._store()
        store.setValue("aperture", self.aperture.currentIndex())
        store.setValue("blur", self.blur_slider.value())
        store.setValue("bokeh", self.bokeh_slider.value())
        store.setValue("bokeh_shape", self.bokeh_shape.currentIndex())
        store.setValue("bokeh_density", self.bokeh_density.value())
        store.setValue("bokeh_size", self.bokeh_size.value())
        store.setValue("full_luminance_bokeh", self.full_luminance_bokeh.isChecked())
        store.setValue("parked_bokeh_values", self._parked_bokeh_values)
        store.setValue("spherical", self.spherical_slider.value())
        store.setValue("cat_eye", self.cat_eye_slider.value())
        store.setValue("focus", round(self.focus_slider.value() / _FOCUS_VALUE_SCALE))
        store.setValue("focus_tenths", self.focus_slider.value())
        store.setValue("depth_strength", self.depth_strength.value())
        store.setValue("protect_subject", self.keep_subject.isChecked())
        store.setValue("subject_expand", self.subject_expand_slider.value())
        store.setValue("subject_feather", self.subject_feather_slider.value())
        store.setValue("highlight_rolloff", self.highlight_check.isChecked())
        store.setValue("highlight_threshold", round(self.highlight_slider.value() / _FOCUS_VALUE_SCALE))
        store.setValue("highlight_threshold_tenths", self.highlight_slider.value())
        store.setValue("preview_scale", self.preview_scale.currentIndex())
        store.setValue("depth_mode", self.depth_mode.currentIndex())
        store.setValue("depth_size_index", self.depth_size.currentIndex())
        store.setValue("model", self.model_combo.currentData() or "")
        store.setValue("card_order", ",".join(self.card_reorder.order()))
        store.setValue("geometry", self.saveGeometry())
        store.sync()

    def _set_bokeh_ranges(self, expanded, *, remember=True):
        for name, (normal_range, extended_range) in _BOKEH_RANGES.items():
            slider = getattr(self, name)
            minimum, maximum = extended_range if expanded else normal_range
            value = slider.value()
            if remember and not expanded and not minimum <= value <= maximum:
                self._parked_bokeh_values[name] = value
            slider.blockSignals(True)
            slider.setRange(minimum, maximum)
            if remember and expanded:
                slider.setValue(self._parked_bokeh_values.pop(name, value))
            slider.blockSignals(False)

        self._set_bokeh_choices(
            "aperture", len(_BASE_F_STOPS),
            [(f"f/{stop}", stop) for stop in _EXTENDED_F_STOPS], expanded, remember,
        )
        self._set_bokeh_choices(
            "bokeh_shape", 6, [(f"{blades} 边形光圈", blades) for blades in _EXTENDED_BLADES],
            expanded, remember,
        )

    def _set_bokeh_choices(self, name, standard_count, extra, expanded, remember):
        combo = getattr(self, name)
        index = combo.currentIndex()
        combo.blockSignals(True)
        if expanded and combo.count() == standard_count:
            for label, value in extra:
                combo.addItem(label, value)
            if remember:
                selected = self._parked_bokeh_values.pop(name, index)
                if 0 <= selected < combo.count():
                    combo.setCurrentIndex(selected)
        elif not expanded and combo.count() > standard_count:
            if index >= standard_count:
                if remember:
                    self._parked_bokeh_values[name] = index
                selected = combo.currentData()
                closest = min(range(standard_count), key=lambda i: abs(combo.itemData(i) - selected))
                combo.setCurrentIndex(closest)
            while combo.count() > standard_count:
                combo.removeItem(standard_count)
        combo.blockSignals(False)

    def _on_bokeh_mode_changed(self, expanded):
        self._set_bokeh_ranges(expanded)
        self.on_settings_changed()

    @staticmethod
    def _update_slider_readout(slider, readout):
        if isinstance(readout, (QSpinBox, QDoubleSpinBox)):
            scale = 10**readout.decimals() if isinstance(readout, QDoubleSpinBox) else 1
            readout.blockSignals(True)
            if scale == 1:
                readout.setRange(slider.minimum(), slider.maximum())
                readout.setValue(slider.value())
            else:
                readout.setRange(slider.minimum() / scale, slider.maximum() / scale)
                readout.setValue(slider.value() / scale)
            readout.blockSignals(False)
        else:
            readout.setText(str(slider.value()))

    def make_slider(self, minimum, maximum, value, *, editable=False, suffix="", decimals=0):
        scale = 10**decimals
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(minimum * scale, maximum * scale)
        slider.setValue(value * scale)
        slider.setPageStep(10 * scale)
        if editable:
            readout = QDoubleSpinBox() if decimals else QSpinBox()
            if decimals:
                readout.setDecimals(decimals)
                readout.setSingleStep(1 / scale)
            readout.setObjectName("LensValue")
            readout.setRange(minimum, maximum)
            readout.setValue(value)
            readout.setSuffix(suffix)
            readout.setFixedWidth(64)
            readout.setAlignment(Qt.AlignmentFlag.AlignRight)
            # Qt's stylesheet geometry can collapse the edit field with
            # NoButtons. Hide arrow subcontrols in QSS instead.
            readout.setButtonSymbols(QSpinBox.ButtonSymbols.UpDownArrows)
            readout.lineEdit().setTextMargins(6, 0, 6, 0)
            readout.setKeyboardTracking(False)
            readout.setToolTip("输入数值后按 Enter 确认，可用方向键逐级调整。")
            readout.valueChanged.connect(lambda value: slider.setValue(round(value * scale)))
            return slider, readout
        label = QLabel(str(value))
        label.setMinimumWidth(28)
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return slider, label

    def slider_row(self, slider, label):
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(slider, 1)
        layout.addWidget(label)
        return widget

    def refresh_model_labels(self):
        current = self.model_combo.currentData()
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for spec in MODEL_SPECS:
            status = "已下载" if resolve_model_path(spec.repo) else "首次需下载"
            # DA3 定位成"更准"的那一档，标签里直接写出来，避免用户只看到两个陌生名字
            tag = ""
            self.model_combo.addItem(
                f"{spec.name}{tag} · {spec.size_label} · {status}", spec.repo
            )
            self.model_combo.setItemData(
                self.model_combo.count() - 1,
                f"{spec.license} 许可 · {spec.repo}",
                Qt.ItemDataRole.ToolTipRole,
            )
        for item in imported_models():
            ref = item["ref"]
            if resolve_model_path(ref) is not None:
                status = "本地可用"
            elif is_local_ref(ref):
                status = "目录不可用"
            else:
                status = "首次需下载"
            self.model_combo.addItem(f"{item['name']}（导入，{status}）", ref)
        if current:
            index = self.model_combo.findData(current)
            if index >= 0:
                self.model_combo.setCurrentIndex(index)
        self.model_combo.blockSignals(False)
        self.on_model_changed()

    def on_model_changed(self, *_):
        """选中 DA3 时说明状态：缺依赖 / 需要下载 / 已就绪。"""
        if not hasattr(self, "ai_hint"):
            return
        spec = model_spec(self.model_combo.currentData() or "")
        if spec.backend != "da3":
            if self.depth is None:
                self.ai_hint.setText("尚未运行，将使用中心对焦")
            return
        if importlib.util.find_spec("depth_anything_3") is None:
            self.ai_hint.setText(
                "DA3 需要额外安装依赖，请参阅项目说明中的“Depth Anything 3”章节。"
            )
        elif resolve_model_path(spec.repo) is None:
            self.ai_hint.setText(f"DA3 模型尚未下载，首次运行时需要下载{spec.size_label}的数据。")
        else:
            self.ai_hint.setText("DA3 模型已就绪，可运行深度估算。")

    def import_model(self):
        dialog = ModelImportDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        kind, value, name = dialog.values()

        if kind == "local":
            ref = str(Path(os.path.expanduser(value)))
            if not is_complete_model_dir(ref):
                QMessageBox.critical(
                    self, "导入失败",
                    "该文件夹不是可用的深度模型：\n\n" + ref + "\n\n"
                    "文件夹需要包含 config.json，以及权重文件 "
                    "model.safetensors 或 pytorch_model.bin。",
                )
                return
            label = name or Path(ref).name
            hint = "本地模型，直接使用"
        else:
            ref = normalize_repo(value)
            if not ref or "/" not in ref:
                QMessageBox.critical(
                    self, "导入失败",
                    "请填写形如 owner/name 的仓库 ID，或完整的模型页面链接。",
                )
                return
            label = name or ref
            hint = "首次运行 AI 估算时会自动联网下载"

        add_imported_model(label, ref)
        self.refresh_model_labels()
        index = self.model_combo.findData(ref)
        if index >= 0:
            self.model_combo.setCurrentIndex(index)
        self.ai_hint.setText(f"已导入模型：{label}（{hint}）。")
        self.statusBar().showMessage(f"已导入模型：{label}", 4000)

    def open_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "打开图片", "", "图片 (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp)"
        )
        if not path:
            return
        self.open_image_path(path)

    def open_image_path(self, path):
        """从磁盘加载一张图；对话框和拖拽导入共用这条路径。"""
        try:
            signature = source_signature(path)
            image = load_image_rgb(path)
            if source_signature(path) != signature:
                signature = None
        except Exception as exc:
            QMessageBox.critical(self, "打开失败", str(exc))
            return False
        if hasattr(self, "welcome_page") and self.pages.currentWidget() is self.welcome_page:
            self._enter_workspace("start")
        self.image_path = path
        self._image_source_signature = signature
        self.original_full = image
        self._depth_full = None
        self._depth_name = None
        self.processed = None
        self.depth_preview.set_depth(None)
        self.compare_btn.setChecked(False)
        self.reload_preview_size()
        self.setWindowTitle(
            f"{APP_TITLE}  v{APP_VERSION} · {APP_EDITION} · {Path(path).name}"
        )
        self._refresh_action_roles()
        self.statusBar().showMessage(
            f"已打开：{Path(path).name}（{image.shape[1]}×{image.shape[0]}）", 4000
        )
        self.event_log.log(
            "image.open", path=str(path),
            size=f"{image.shape[1]}x{image.shape[0]}",
            preview=self.preview_scale.currentText(),
        )
        self._record("打开图片", immediate=True)
        return True

    # ------------------------------------------------------------------
    # 拖拽导入
    # ------------------------------------------------------------------

    @staticmethod
    def _drag_paths(mime):
        if not mime.hasUrls():
            return []
        paths = []
        for url in mime.urls():
            if not url.isLocalFile():
                continue
            path = url.toLocalFile()
            if Path(path).suffix.lower() in IMAGE_SUFFIXES:
                paths.append(path)
        return paths

    def _set_drag_hint(self, active):
        canvases=[self.before_canvas,self.after_canvas]
        if hasattr(self,"light_workspace"):
            canvases.extend(preview.canvas for preview in self.light_workspace.previews.values())
        for canvas in canvases:
            canvas.setProperty("drag", "true" if active else "false")
            canvas.style().unpolish(canvas)
            canvas.style().polish(canvas)
            canvas.update()
        if active:
            self.statusBar().showMessage("松开鼠标即可导入图片", 0)
        else:
            self.statusBar().clearMessage()

    def dragEnterEvent(self, event):
        if self._drag_paths(event.mimeData()):
            event.acceptProposedAction()
            self._set_drag_hint(True)
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._set_drag_hint(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self._set_drag_hint(False)
        paths = self._drag_paths(event.mimeData())
        if not paths:
            event.ignore()
            return
        event.acceptProposedAction()
        self.open_image_path(paths[0])

    def reload_preview_size(self, *_):
        if self.original_full is None:
            return
        self._invalidate_preview()
        image = self.original_full
        mode = self.preview_scale.currentIndex()
        max_side = [1000, 1400, max(image.shape[:2])][mode]
        h, w = image.shape[:2]
        scale = min(1.0, max_side / max(h, w))
        if scale < 1:
            image = cv2.resize(image, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        self.original = image

        # AI depth is tied to the preview resolution; an imported full-resolution
        # depth map can simply be rescaled for the new preview size.
        self._ai_generation += 1
        self.depth = None
        self.ai_btn.setText("运行 AI 深度估算")
        if self._depth_full is not None:
            self.depth = cv2.resize(
                self._depth_full,
                (self.original.shape[1], self.original.shape[0]),
                interpolation=cv2.INTER_AREA,
            )
            self.ai_hint.setText(f"已导入深度图：{self._depth_name}，已按新预览尺寸缩放。")
        elif self.depth_worker is not None and self.depth_worker.isRunning():
            self.ai_hint.setText("预览已改变，正在运行的估算结果将被丢弃")
        else:
            self.ai_hint.setText("尚未运行，将使用中心对焦")

        self.depth_preview.set_depth(self.depth)
        self.before_canvas.set_image(self.original)
        self.processed = None
        self.after_canvas.set_image(self.original)
        self.update_histogram()
        self._record("切换 预览质量")
        self.render()

    def settings(self):
        return EngineSettings(
            blur_strength=self.blur_slider.value() / 100.0,
            bokeh_strength=self.bokeh_slider.value() / 100.0,
            aperture=float(self.aperture.currentData()),
            focus=self.focus_slider.value() / (100.0 * _FOCUS_VALUE_SCALE),
            depth_strength=self.depth_strength.value() / 100.0,
            protect_subject=self.keep_subject.isChecked(),
            bokeh_blades=int(self.bokeh_shape.currentData()),
            bokeh_density=self.bokeh_density.value() / 100.0,
            bokeh_size=self.bokeh_size.value() / 100.0,
            full_luminance_bokeh=self.full_luminance_bokeh.isChecked(),
            spherical=self.spherical_slider.value() / 100.0,
            cat_eye=self.cat_eye_slider.value() / 100.0,
            subject_expand=self.subject_expand_slider.value() / 100.0,
            subject_feather=self.subject_feather_slider.value() / 100.0,
            highlight_rolloff=self.highlight_check.isChecked(),
            highlight_threshold=self.highlight_slider.value() / (100.0 * _FOCUS_VALUE_SCALE),
        )

    # -- 预览显示（效果范围高亮 / 直方图） ----------------------------------

    def _effect_mask(self):
        """被效果改动过的像素遮罩（预览专用，值为 0 或 255）。"""
        if self.processed is None or self.original is None:
            return None
        if self.processed.shape[:2] != self.original.shape[:2]:
            return None
        diff = cv2.absdiff(self.processed, self.original)
        unchanged = cv2.inRange(diff, (0, 0, 0), (3, 3, 3))
        return cv2.bitwise_not(unchanged, dst=unchanged)

    def _display_image(self):
        """效果预览显示的图：可选叠加半透明红色标出效果范围。"""
        if self.compare_btn.isChecked():
            return self.original
        base = self.processed
        if base is None or not self.range_check.isChecked():
            return base
        mask = self._effect_mask()
        if mask is None or not cv2.countNonZero(mask):
            return base
        return cv2.copyTo(cv2.LUT(base, _EFFECT_TINT_LUT), mask, base.copy())

    def refresh_display(self, *_):
        """刷新效果预览（保持缩放/平移）并同步直方图。"""
        self.after_canvas.set_image(self._display_image(), keep_view=True)
        self.update_histogram()
        self._sync_light_workspace()

    def update_histogram(self, *_):
        # Statistics follow the displayed source/effect, before range tinting.
        self.histogram.set_image(
            self.original if self.compare_btn.isChecked() else
            self.processed if self.processed is not None else self.original
        )

    # -- 日志与使用报告 ------------------------------------------------------

    def _device_profile(self):
        profile = collect_device_profile()
        profile["qt"] = QT_VERSION
        profile["backend"] = self.engine.backend_description
        return profile

    def log_device_profile(self):
        """Record the device once after entering the workspace, never during welcome."""
        if self._closing or self._device_profile_logged:
            return
        if hasattr(self, "welcome_page") and self.pages.currentWidget() is self.welcome_page:
            return
        self._device_profile_logged = True
        profile = self._device_profile()
        signature = device_signature(profile)
        store = self._store()
        previous = str(store.value("device_signature") or "")
        if previous != signature:
            self.event_log.log(
                "device.profile", changed=bool(previous), signature=signature,
                **profile,
            )
            store.setValue("device_signature", signature)
            store.sync()
        else:
            self.event_log.log("device.same", signature=signature)

    def export_usage_report(self):
        """设备信息 + 事件日志拼成一个 txt，方便用户发给开发者。"""
        profile = self._device_profile()
        lines = [
            "伯爵大光圈模拟 · 使用报告",
            f"版本：v{APP_VERSION}",
            f"生成时间：{timestamp()}",
            f"日志文件：{self.event_log.path}",
            "",
            "== 设备与运行环境 ==",
        ]
        lines += [f"{key}: {value}" for key, value in profile.items()]
        lines += [
            "",
            "== 事件日志（时间 / 处理参数 / 耗时）==",
            self.event_log.read_all() or "（本次暂无日志）",
        ]
        target = self.event_log.directory / f"使用报告-{time.strftime('%Y%m%d-%H%M%S')}.txt"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError as exc:
            self.event_log.log("report.error", error=str(exc))
            QMessageBox.critical(self, "导出失败", f"无法写入报告文件：\n{exc}")
            return
        self.event_log.log("report.export", path=str(target))
        QMessageBox.information(
            self, "使用报告已导出",
            f"报告已保存至：\n{target}\n\n报告包含设备信息与处理日志，可提供给开发者用于排查问题。"
        )
        reveal_in_explorer(str(target))

    def on_settings_changed(self, *_):
        for slider, label in [
            (self.blur_slider, self.blur_value), (self.bokeh_slider, self.bokeh_value),
            (self.bokeh_density, self.bokeh_density_value),
            (self.bokeh_size, self.bokeh_size_value),
            (self.focus_slider, self.focus_value),
            (self.depth_strength, self.depth_strength_value),
            (self.spherical_slider, self.spherical_value),
            (self.cat_eye_slider, self.cat_eye_value),
            (self.subject_expand_slider, self.subject_expand_value),
            (self.subject_feather_slider, self.subject_feather_value),
            (self.highlight_slider, self.highlight_value)
        ]:
            self._update_slider_readout(slider, label)
        for slider, label in (
            (self.subject_expand_slider, self.subject_expand_value),
        ):
            value = slider.value()
            label.setText(f"+{value}" if value > 0 else str(value))
        self._record(self._control_label())
        self.schedule_render()
        self._sync_light_workspace()

    def _invalidate_preview(self):
        self._preview_job = -1
        self.render_worker.cancel_previews()

    def schedule_render(self, *_):
        if self._closing:
            return
        self._invalidate_preview()
        self._render_timer.start()

    def on_depth_size_changed(self, *_):
        """Resolution only affects the next AI run, so just tell the user."""
        if self.original is not None:
            self.statusBar().showMessage(
                "深度图分辨率已更改，将在下次运行 AI 深度估算时生效。", 4000
            )
        self._record("切换 深度图分辨率")

    def on_depth_mode_changed(self, *_):
        mode = self.depth_mode.currentIndex()
        if mode == MODE_AI and self.original is not None and self.depth is None:
            self.statusBar().showMessage("深度图尚未生成或导入：当前预览使用中心对焦。", 6000)
        elif mode == MODE_MANUAL:
            self.statusBar().showMessage("点击预览中的主体位置可设置焦点。", 6000)
        self._record("切换 对焦模式")
        self.schedule_render()

    def on_canvas_clicked(self, x, y):
        if self.original is None:
            return
        h, w = self.original.shape[:2]
        if self.depth is not None:
            depth = cv2.resize(self.depth.astype(np.float32), (w, h), interpolation=cv2.INTER_LINEAR)
        else:
            depth = self.engine.center_depth(h, w)
        ix = min(w - 1, max(0, int(x * w)))
        iy = min(h - 1, max(0, int(y * h)))
        focus = float(depth[iy, ix])

        self.depth_mode.blockSignals(True)
        self.depth_mode.setCurrentIndex(MODE_MANUAL)
        self.depth_mode.blockSignals(False)
        self.focus_slider.blockSignals(True)
        self.focus_slider.setValue(round(focus * 100 * _FOCUS_VALUE_SCALE))
        self.focus_slider.blockSignals(False)
        self._update_slider_readout(self.focus_slider, self.focus_value)
        self.statusBar().showMessage(f"焦点深度已设为 {self.focus_value.value():.1f}（点击位置）", 3000)
        self._record("点击设置焦点", immediate=True)
        self.render()

    def toggle_original(self, checked):
        # 切换对比保持缩放/平移（keep_view）：放大看细节时视图不会跳回全图。
        self.compare_btn.setText("显示效果图" if checked else "显示原图")
        self.effect_title.setText("原图对比" if checked else "效果图")
        self.refresh_display()

    def render(self, *_):
        self._render_timer.stop()
        if self._closing or self.original is None:
            return
        mode = self.depth_mode.currentIndex()
        depth = self.depth if mode in (MODE_AI, MODE_MANUAL) and self.depth is not None else None
        self.statusBar().showMessage("正在处理中…", 0)
        self._render_started = time.monotonic()
        self.event_log.log(
            "render.request",
            image=f"{self.original.shape[1]}x{self.original.shape[0]}",
            mode=mode,
            depth="yes" if depth is not None else "none",
            preview=self.preview_scale.currentIndex(),
            params=asdict(self.settings()),
        )
        try:
            self._preview_job = self.render_worker.submit(
                self.original, depth, self.settings(), mode=mode, kind="preview"
            )
        except Exception as exc:
            self.statusBar().showMessage(f"渲染失败：{exc}", 6000)
            self.event_log.log("render.error", error=str(exc))

    def on_render_finished(self, payload):
        """Show a finished preview; exports are handled by their own waiter."""
        job, result, error = payload
        if job.kind != "preview" or job.job_id != self._preview_job:
            return
        if error is not None:
            self.statusBar().showMessage(f"渲染失败：{error}", 6000)
            self.event_log.log("render.error", error=str(error))
            return
        elapsed = time.monotonic() - self._render_started if self._render_started else 0.0
        self._render_started = None
        self.processed = result
        self.refresh_display()
        self.status_label.setText(self.engine.backend_description)
        self.statusBar().showMessage(f"处理完成 · 用时 {elapsed:.2f}s", 4000)
        self.event_log.log(
            "render.done",
            out=f"{result.shape[1]}x{result.shape[0]}",
            elapsed=round(elapsed, 3),
        )

    def _run_render_job(self, image, depth, settings, mode,
                        kind="export", return_layers=False):
        """Run one job on the render thread and wait for it here.

        The GUI event loop keeps running while we wait (a nested QEventLoop), so
        the window stays responsive during a long export instead of freezing.
        """
        token = object()
        state = {"result": None, "error": None, "timed_out": False}
        loop = QEventLoop(self)

        def on_result(payload):
            job, result, error = payload
            if job.tag is token:
                state["result"] = result
                state["error"] = error
                loop.quit()

        # Safety net: never wait forever if the render thread fails to report.
        timeout = QTimer(self)
        timeout.setSingleShot(True)
        timeout.timeout.connect(lambda: (state.__setitem__("timed_out", True), loop.quit()))

        self.render_worker.finished_job.connect(on_result)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        self._export_waiters += 1
        timeout.start(60000)
        try:
            self.render_worker.submit(
                image, depth, settings, mode,
                kind=kind, return_layers=return_layers, tag=token,
            )
            loop.exec()
        finally:
            self._export_waiters -= 1
            timeout.stop()
            try:
                self.render_worker.finished_job.disconnect(on_result)
            except (RuntimeError, TypeError):
                pass
            QApplication.restoreOverrideCursor()
        if state["result"] is None and state["error"] is None:
            raise TimeoutError("渲染超时：60 秒内没有返回结果。")
        if state["error"] is not None:
            raise state["error"]
        return state["result"]

    def run_ai_depth(self):
        if self.original is None:
            QMessageBox.information(self, "提示", "请先打开一张图片。")
            return
        if self.depth_worker is not None and self.depth_worker.isRunning():
            return

        self._ai_generation += 1
        model_id = self.model_combo.currentData() or DEFAULT_MODEL
        worker = DepthWorker(
            self.original.copy(),
            generation=self._ai_generation,
            model_id=model_id,
            depth_size=int(self.depth_size.currentData() or 0),
        )
        self.depth_worker = worker
        worker.finished_depth.connect(lambda depth, w=worker: self.ai_finished(depth, w))
        worker.failed.connect(lambda message, w=worker: self.ai_failed(message, w))
        worker.progress.connect(lambda text, w=worker: self.ai_progress(text, w))
        worker.finished.connect(self._sync_light_workspace)

        self.ai_btn.setEnabled(False)
        self.ai_btn.setText("正在估算深度…")
        self.model_combo.setEnabled(False)
        self.import_model_btn.setEnabled(False)
        self.import_depth_btn.setEnabled(False)
        self.depth_size.setEnabled(False)
        self.ai_hint.setText(f"正在准备模型：{model_label(model_id)}…")
        self.statusBar().showMessage("正在处理中… AI 深度估算", 0)
        self._ai_started = time.monotonic()
        self.event_log.log(
            "ai.start", model=model_id, resolution=self.depth_size.currentText(),
            image=f"{self.original.shape[1]}x{self.original.shape[0]}",
        )
        worker.start()
        self._sync_light_workspace()

    def ai_progress(self, text, worker):
        if worker is not self.depth_worker:
            return
        self.ai_hint.setText(text)
        self.statusBar().showMessage(text)
        self._sync_light_workspace()

    def import_depth(self):
        if self.original is None or self.original_full is None:
            QMessageBox.information(self, "提示", "请先打开一张图片。")
            return
        start_dir = str(Path(self.image_path).parent) if self.image_path else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "导入深度图", start_dir,
            "深度图 (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp)"
        )
        if not path:
            return
        try:
            depth = load_depth_map(path)
        except Exception as exc:
            QMessageBox.critical(self, "导入失败", str(exc))
            return

        h, w = self.original_full.shape[:2]
        if depth.shape[:2] != (h, w):
            depth = cv2.resize(depth, (w, h), interpolation=cv2.INTER_CUBIC)
        self.apply_depth_map(depth, Path(path).name)
        self.statusBar().showMessage(f"已导入深度图：{Path(path).name}", 4000)

    def apply_depth_map(self, depth_full, name):
        """Use a full-resolution depth map for rendering and full-size export."""
        self._depth_full = np.asarray(depth_full, dtype=np.float32)
        self._depth_name = name
        self.depth = cv2.resize(
            self._depth_full,
            (self.original.shape[1], self.original.shape[0]),
            interpolation=cv2.INTER_AREA,
        )
        self.depth_preview.set_depth(self.depth)

        self.depth_mode.blockSignals(True)
        self.depth_mode.setCurrentIndex(MODE_AI)
        self.depth_mode.blockSignals(False)

        focus = self._auto_focus(self.depth)
        self.focus_slider.blockSignals(True)
        self.focus_slider.setValue(round(focus * 100 * _FOCUS_VALUE_SCALE))
        self.focus_slider.blockSignals(False)
        self._update_slider_readout(self.focus_slider, self.focus_value)

        self.ai_btn.setText("运行 AI 深度估算")
        self.ai_hint.setText(f"已导入深度图：{name}；点击预览可微调焦点。")
        self.event_log.log(
            "depth.import", name=str(name),
            size=f"{self.depth.shape[1]}x{self.depth.shape[0]}",
        )
        self._record("导入深度图", immediate=True)
        self.render()

    def export_depth(self):
        if self.depth is None and self._depth_full is None:
            QMessageBox.information(
                self, "提示",
                "尚无深度图，请先运行 AI 深度估算或导入深度图。"
            )
            return

        preferred = Path(self.image_path).parent if self.image_path else None
        target_dir = choose_export_directory(preferred)
        stem = safe_stem(self.image_path) if self.image_path else "深度图"
        default_path = str(target_dir / f"{stem}_深度图.png")
        if preferred is not None and target_dir != preferred:
            self.statusBar().showMessage(
                f"原目录不可写，导出位置已改为：{target_dir}", 6000
            )
        path, selected = QFileDialog.getSaveFileName(
            self, "导出深度图", default_path,
            "16 位 PNG (*.png);;16 位 TIFF (*.tif *.tiff);;8 位 JPEG (*.jpg *.jpeg)"
        )
        if not path:
            return

        self.statusBar().showMessage("正在处理中… 导出深度图", 0)
        QApplication.processEvents()
        started = time.monotonic()
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            ext = os.path.splitext(path)[1].lower()
            if ext not in (".png", ".tif", ".tiff", ".jpg", ".jpeg"):
                if "TIFF" in selected.upper():
                    ext = ".tif"
                elif "JPEG" in selected.upper():
                    ext = ".jpg"
                else:
                    ext = ".png"
                path += ext

            if self._depth_full is not None:
                depth = self._depth_full
            elif self.depth is not None:
                depth = self.depth
            else:
                raise ValueError("没有可导出的深度图。")

            h, w = self.original_full.shape[:2]
            if depth.shape[:2] != (h, w):
                depth = cv2.resize(
                    depth.astype(np.float32), (w, h), interpolation=cv2.INTER_CUBIC
                )
            save_depth_map(path, depth, ext)
            bits = "16 位" if ext in (".png", ".tif", ".tiff") else "8 位"
            elapsed = time.monotonic() - started
            self.event_log.log(
                "export.depth", path=str(path), bits=bits, out=f"{w}x{h}",
                elapsed=round(elapsed, 3),
            )
            self.statusBar().showMessage(f"导出完成 · 用时 {elapsed:.2f}s", 4000)
            QMessageBox.information(
                self, "导出完成",
                f"已导出{bits}深度图：\n{path}\n分辨率：{w}×{h}"
            )
            if not reveal_in_explorer(path):
                self.statusBar().showMessage("已导出，但未能自动打开文件夹。", 6000)
        except PermissionError as exc:
            self.event_log.log("export.error", kind="permission", path=str(path))
            QMessageBox.critical(
                self, "导出失败",
                "无法写入目标路径：\n" + str(path) + "\n\n"
                "请检查文件权限，或选择其他可写目录。\n\n"
                + str(exc)
            )
        except Exception as exc:
            self.event_log.log("export.error", error=str(exc))
            QMessageBox.critical(self, "导出失败", str(exc))

    def _auto_focus(self, depth):
        h, w = depth.shape[:2]
        cy, cx = h * 0.46, w * 0.5
        r = max(8, int(min(h, w) * 0.12))
        y0, y1 = int(max(0, cy - r)), int(min(h, cy + r))
        x0, x1 = int(max(0, cx - r)), int(min(w, cx + r))
        region = depth[y0:y1, x0:x1]
        value = float(np.nanmedian(region)) if region.size else float(np.nanmedian(depth))
        if not np.isfinite(value):
            value = 0.5
        return float(np.clip(value, 0.0, 1.0))

    def ai_finished(self, depth, worker):
        if worker is not self.depth_worker:
            return
        self.depth_worker = None
        self.ai_btn.setEnabled(True)
        self.ai_btn.setText("运行 AI 深度估算")
        self.model_combo.setEnabled(True)
        self.import_model_btn.setEnabled(True)
        self.import_depth_btn.setEnabled(True)
        self.depth_size.setEnabled(True)
        self.refresh_model_labels()

        if worker.generation != self._ai_generation or self.original is None:
            self.ai_hint.setText("图片或预览尺寸已变化，本次结果已丢弃。")
            self.statusBar().showMessage("AI 深度结果已过期，已丢弃。", 5000)
            return

        if depth.shape[:2] != self.original.shape[:2]:
            depth = cv2.resize(
                depth.astype(np.float32),
                (self.original.shape[1], self.original.shape[0]),
                interpolation=cv2.INTER_LINEAR,
            )
        self.depth = depth.astype(np.float32)
        self._depth_full = None
        self._depth_name = None
        self.depth_preview.set_depth(self.depth)

        self.depth_mode.blockSignals(True)
        self.depth_mode.setCurrentIndex(MODE_AI)
        self.depth_mode.blockSignals(False)

        focus = self._auto_focus(self.depth)
        self.focus_slider.blockSignals(True)
        self.focus_slider.setValue(round(focus * 100 * _FOCUS_VALUE_SCALE))
        self.focus_slider.blockSignals(False)
        self._update_slider_readout(self.focus_slider, self.focus_value)

        self.ai_btn.setText("重新运行 AI 深度估算")
        self.ai_hint.setText(
            f"已用 {model_label(worker.model_id)} 生成深度图；"
            "焦点已自动对准画面中部，可点击预览微调。"
        )
        elapsed = time.monotonic() - self._ai_started if self._ai_started else 0.0
        self._ai_started = None
        self.statusBar().showMessage(f"处理完成 · 用时 {elapsed:.2f}s", 5000)
        self.event_log.log(
            "ai.done", out=f"{self.depth.shape[1]}x{self.depth.shape[0]}",
            elapsed=round(elapsed, 3),
        )
        self._record("运行 AI 深度估算", immediate=True)
        self.render()

    def ai_failed(self, message, worker):
        if worker is not self.depth_worker:
            return
        self.depth_worker = None
        self.ai_btn.setEnabled(True)
        self.ai_btn.setText("运行 AI 深度估算")
        self.model_combo.setEnabled(True)
        self.import_model_btn.setEnabled(True)
        self.import_depth_btn.setEnabled(True)
        self.depth_size.setEnabled(True)
        self.refresh_model_labels()
        if worker.generation != self._ai_generation:
            self.ai_hint.setText("图片或预览尺寸已变化，本次估算结果已丢弃。")
            return
        if self.depth is not None:
            self.ai_hint.setText("本次估算失败，继续使用上一次的深度图。")
        else:
            self.ai_hint.setText("未生成深度图，继续使用中心对焦。")
        self.event_log.log("ai.fail", error=str(message)[:300])
        QMessageBox.warning(self, "AI 深度估算不可用", message)

    def export_image(self):
        if self.original is None or self.original_full is None:
            QMessageBox.information(self, "提示", "请先打开一张图片。")
            return

        full_h, full_w = self.original_full.shape[:2]
        dialog = ExportDialog(self, full_w, full_h)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        options = dialog.values()

        layer = bool(options["layer"])
        fmt = "PNG" if layer else options["format"]
        scale = float(options["scale"])
        out_w = max(1, int(round(full_w * scale)))
        out_h = max(1, int(round(full_h * scale)))

        preferred = Path(self.image_path).parent if self.image_path else None
        target_dir = choose_export_directory(preferred)
        stem = safe_stem(self.image_path) if self.image_path else "伯爵大光圈效果"
        suffix = "_大光圈图层" if layer else "_大光圈"
        extension = ".png" if fmt == "PNG" else ".jpg"
        default_path = str(target_dir / f"{stem}{suffix}{extension}")
        if preferred is not None and target_dir != preferred:
            self.statusBar().showMessage(
                f"原目录不可写，导出位置已改为：{target_dir}", 6000
            )

        if layer:
            file_filter = "PNG 透明图层 (*.png)"
        elif fmt == "JPEG":
            file_filter = "JPEG 图片 (*.jpg *.jpeg)"
        else:
            file_filter = "PNG 图片 (*.png)"
        path, _selected = QFileDialog.getSaveFileName(
            self, "导出效果图", default_path, file_filter
        )
        if not path:
            return

        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            if fmt == "PNG":
                if not path.lower().endswith(".png"):
                    path = os.path.splitext(path)[0] + ".png"
            else:
                if not path.lower().endswith((".jpg", ".jpeg")):
                    path = os.path.splitext(path)[0] + ".jpg"

            if scale == 1.0:
                work_image = self.original_full
            else:
                work_image = cv2.resize(
                    self.original_full, (out_w, out_h),
                    interpolation=cv2.INTER_AREA,
                )

            mode = self.depth_mode.currentIndex()
            depth = None
            if mode in (MODE_AI, MODE_MANUAL):
                source_depth = (
                    self._depth_full if self._depth_full is not None else self.depth
                )
                if source_depth is not None:
                    if source_depth.shape[:2] != (out_h, out_w):
                        depth = cv2.resize(
                            source_depth.astype(np.float32),
                            (out_w, out_h),
                            interpolation=cv2.INTER_LINEAR,
                        )
                    else:
                        depth = source_depth.astype(np.float32)

            settings = self.settings()
            settings.bokeh_aa = int(options["aa"])

            self.statusBar().showMessage(f"正在处理中… 导出 {out_w}×{out_h}", 0)
            started = time.monotonic()
            self.event_log.log(
                "export.request", path=str(path), format=fmt, scale=scale,
                aa=int(options["aa"]), quality=int(options["quality"]), layer=layer,
                out=f"{out_w}x{out_h}", mode=mode, params=asdict(settings),
            )

            out = self._run_render_job(
                work_image, depth, settings, mode,
                kind="export", return_layers=layer,
            )
            if layer:
                _final, effect, alpha = out
                save_rgba_png(path, effect, alpha)
                note = "\n\n透明图层包含虚化和光斑效果，叠加至原图后可还原效果。"
            else:
                save_image_file(
                    path, out, fmt, quality=int(options["quality"])
                )
                note = ""

            elapsed = time.monotonic() - started
            self.event_log.log(
                "export.done", path=str(path), out=f"{out_w}x{out_h}",
                elapsed=round(elapsed, 3),
            )
            if mode in (MODE_AI, MODE_MANUAL) and depth is None:
                note += "\n\n注意：未生成深度图，本次使用中心对焦导出。"
            self.statusBar().showMessage(f"导出完成 · 用时 {elapsed:.2f}s", 4000)
            self.show_export_completion(path,note.strip())
        except PermissionError as exc:
            self.event_log.log("export.error", kind="permission", path=str(path))
            QMessageBox.critical(
                self, "导出失败",
                "无法写入目标路径：\n" + str(path) + "\n\n"
                "请检查文件权限，或选择其他可写目录。\n\n"
                + str(exc)
            )
        except Exception as exc:
            self.event_log.log("export.error", error=str(exc))
            QMessageBox.critical(self, "导出失败", str(exc))

    def show_export_completion(self,path,details=''):
        dialog=ExportCompletionDialog(self,path,motion=MOTION,details=details)
        dialog.fileRequested.connect(self._reveal_export)
        self._export_completion_dialog=dialog
        try:
            dialog.exec()
            if dialog.navigation=='exit':self.show_welcome()
        finally:
            self._export_completion_dialog=None
            dialog.deleteLater()

    def _reveal_export(self,path):
        if not reveal_in_explorer(path):
            self.statusBar().showMessage('无法定位导出文件，请检查文件是否存在。',6000)

    def closeEvent(self, event):
        if self._export_waiters or not self.render_worker.stop():
            self.statusBar().showMessage("正在导出图片，请等待导出完成后关闭窗口。", 4000)
            event.ignore()
            return
        self._closing = True
        self._preview_job = -1
        self._render_timer.stop()
        worker = self.depth_worker
        if worker is not None and worker.isRunning():
            self.statusBar().showMessage("正在结束 AI 任务，请稍候…")
            QApplication.processEvents()
            worker.requestInterruption()
            if not worker.wait(5000):
                worker.terminate()
                worker.wait(1000)
        self.depth_worker = None
        if self._workspace_entered and self.is_workspace_active():
            self._save_session()
        self._save_config()
        self.event_log.log("app.exit", actions=len(self.history))
        super().closeEvent(event)


if __name__ == "__main__":
    # 打包后的自检入口：不弹窗、跑一遍完整初始化就退出，用来验证 exe 不缺资源
    selftest = "--selftest" in sys.argv
    if selftest:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setWindowIcon(QIcon(str(APP_ICON)))
    apply_theme(app)  # 必须在建窗口之前：QPalette + QSS + 内置字体一起装
    window = MainWindow()
    window.show()
    if selftest:
        app.processEvents()
        window.close()  # stop the render/depth threads before exiting
        # --windowed 打包后 stdout 是 None，直接 print 会抛异常
        stream = sys.stdout or sys.stderr
        if stream is not None:
            stream.write(f"selftest ok: {APP_TITLE}\n")
            stream.flush()
        sys.exit(0)
    sys.exit(app.exec())
