import os
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError
from PySide6.QtCore import (
    Qt, QTimer, Signal, QThread, QMutex, QMutexLocker, QWaitCondition, QEventLoop,
    QSettings, QStandardPaths, QPointF, QRectF, QUrl,
)
from PySide6.QtGui import (
    QAction, QDesktopServices, QIcon, QImage, QPainter, QPixmap,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton, QFileDialog,
    QVBoxLayout, QHBoxLayout, QFormLayout, QSlider, QComboBox, QCheckBox,
    QMessageBox, QSplitter, QGroupBox, QStatusBar, QDialog, QDialogButtonBox,
    QLineEdit, QScrollArea, QFrame, QListWidget,
)
from PySide6 import __version__ as QT_VERSION

from engine import BokehEngine, EngineSettings, MODE_AI, MODE_MANUAL
from depth_worker import (
    DEFAULT_MODEL, MODEL_CHOICES, DepthWorker, model_label, resolve_model_path,
    imported_models, add_imported_model, normalize_repo, is_complete_model_dir,
    is_local_ref,
)
from eventlog import (
    EventLog, collect_device_profile, device_signature, timestamp,
)
from history import ActionHistory
from theme import apply_theme, asset
from version import __version__ as APP_VERSION

APP_TITLE = "伯爵大光圈模拟"
# 本次更新的主题标语：跟在界面版本号后面显示，版本号本身不变。
APP_EDITION = "诗人革命"
BRAND_LOGO = asset("brand", "logo-mark.png")
APP_ICON = asset("brand", "app.ico")


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


def brand_pixmap(size):
    """按 2× 渲染 logo，再交给 Qt 按逻辑像素缩放，避免高分屏发虚。"""
    pixmap = QPixmap(str(BRAND_LOGO))
    if pixmap.isNull():
        return pixmap
    ratio = 2.0
    scaled = pixmap.scaled(
        int(size * ratio),
        int(size * ratio),
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    scaled.setDevicePixelRatio(ratio)
    return scaled


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


class RenderWorker(QThread):
    """Run BokehEngine.process() off the GUI thread.

    Rendering a full-resolution image takes seconds, so doing it on the GUI
    thread froze the window ("未响应"). Here the engine runs on its own thread
    and only the finished pixels are handed back. Preview requests are
    coalesced - if several arrive while one is running, only the newest queued
    preview is kept - while export requests are queued and never dropped.
    """

    finished_job = Signal(object)  # (RenderJob, result, error)

    def __init__(self, engine):
        super().__init__()
        self._engine = engine
        self._mutex = QMutex()
        self._wait = QWaitCondition()
        self._queue = []
        self._seq = 0
        self._closing = False

    def submit(self, image, depth, settings, mode, kind="preview",
               return_layers=False, tag=None):
        with QMutexLocker(self._mutex):
            self._seq += 1
            job = RenderJob(
                image, depth, settings, mode, kind, return_layers, self._seq, tag
            )
            if kind == "preview":
                # A newer preview makes any queued preview obsolete.
                self._queue = [j for j in self._queue if j.kind != "preview"]
            self._queue.append(job)
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
            self._mutex.unlock()

            try:
                result = self._engine.process(
                    job.image, job.depth, job.settings,
                    mode=job.mode, return_layers=job.return_layers,
                )
                self.finished_job.emit((job, result, None))
            except Exception as exc:  # noqa: BLE001 - reported to the GUI thread
                self.finished_job.emit((job, None, exc))

    def stop(self):
        self._mutex.lock()
        self._closing = True
        self._mutex.unlock()
        self._wait.wakeAll()
        if not self.wait(3000):
            self.terminate()
            self.wait(1000)


class ZoomableCanvas(QLabel):
    """预览画布基类：Ctrl+滚轮缩放、Ctrl+中键拖拽平移、双击复位。

    缩放/平移只影响显示，不动像素数据；三种预览图（原图、效果图、深度图）
    共用这套交互。普通滚轮不拦截，仍然交给外层滚动区。
    """

    MIN_ZOOM = 0.1
    MAX_ZOOM = 20.0
    ZOOM_HINT = "Ctrl+滚轮缩放 · Ctrl+中键拖拽平移 · 双击复位"

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
        super().__init__("打开一张图片开始")
        self.setMinimumSize(360, 240)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._idle_cursor = Qt.CursorShape.PointingHandCursor

    def set_image(self, image, keep_view=False):
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


class HistogramPlot(QWidget):
    """自绘的 256 档亮度直方图（数据来自当前预览图）。"""

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(88)
        self.setObjectName("HistogramPlot")
        self._counts = None

    def set_image(self, image):
        if image is None:
            self._counts = None
        else:
            gray = cv2.cvtColor(
                np.ascontiguousarray(image[:, :, :3]), cv2.COLOR_RGB2GRAY
            )
            self._counts = cv2.calcHist([gray], [0], None, [256], [0, 256]).ravel()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().color(self.backgroundRole()))
        if self._counts is None or float(self._counts.max()) <= 0:
            return
        width, height = self.width(), self.height()
        peak = float(self._counts.max())
        bar = self.palette().color(self.foregroundRole())
        bar.setAlpha(210)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bar)
        for index in range(256):
            scaled = (float(self._counts[index]) / peak) * max(height - 2.0, 1.0)
            if scaled <= 0.0:
                continue
            x0 = int(index * width / 256)
            x1 = max(x0 + 1, int((index + 1) * width / 256))
            painter.drawRect(x0, int(height - scaled), x1 - x0, int(scaled))


class HistogramPanel(QWidget):
    """直方图面板：右上角 −/+ 折叠或展开。"""

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        header = QHBoxLayout()
        title = QLabel("亮度分布")
        title.setProperty("role", "hint")
        header.addWidget(title)
        header.addStretch()
        self.toggle_btn = QPushButton("−")
        self.toggle_btn.setFixedSize(24, 22)
        self.toggle_btn.setToolTip("折叠 / 展开直方图")
        self.toggle_btn.clicked.connect(self.toggle_collapsed)
        header.addWidget(self.toggle_btn)
        layout.addLayout(header)
        self.plot = HistogramPlot()
        layout.addWidget(self.plot)
        self._collapsed = False

    def toggle_collapsed(self):
        self._collapsed = not self._collapsed
        self.plot.setVisible(not self._collapsed)
        self.toggle_btn.setText("+" if self._collapsed else "−")

    def set_image(self, image):
        self.plot.set_image(image)


class ExportDialog(QDialog):
    """Export options: content, format, resolution, anti-aliasing and quality."""

    def __init__(self, parent, width, height):
        super().__init__(parent)
        self.setWindowTitle("导出设置")
        self.setMinimumWidth(380)
        self._image_width = width
        self._image_height = height

        form = QFormLayout(self)
        form.setContentsMargins(18, 16, 18, 16)
        form.setSpacing(10)

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
            "透明图层只包含虚化/光斑效果，叠在原图上方即可还原；仅支持 PNG。"
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
        form.addRow(buttons)

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


class ModelImportDialog(QDialog):
    """Import a depth model: a local HF-format folder, or a repo id / link."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("导入 AI 深度模型")
        self.setMinimumWidth(460)

        form = QFormLayout(self)
        form.setContentsMargins(18, 16, 18, 16)
        form.setSpacing(10)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("可留空，默认用文件夹名或仓库名")
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
        form.addRow("仓库 ID / 链接", self.repo_edit)

        note = QLabel(
            "两者填一个即可。填本地文件夹：直接使用该模型，不联网。"
            "填仓库 ID 或模型页面链接：本地没有时会在运行 AI 估算时联网下载"
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
        form.addRow(buttons)

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
                "请在“本地模型文件夹”和“仓库 ID / 链接”中只填写一个。",
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
_HISTORY_WIDGETS = (
    ("aperture", "index"),
    ("blur_slider", "value"),
    ("bokeh_slider", "value"),
    ("bokeh_shape", "index"),
    ("bokeh_density", "value"),
    ("bokeh_size", "value"),
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


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_TITLE}  v{APP_VERSION} · {APP_EDITION}")
        self.resize(1280, 800)
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

        # All engine.process() work runs on this thread so a slow full-size
        # render can never block the window (which used to show "未响应").
        self.render_worker = RenderWorker(self.engine)
        self.render_worker.finished_job.connect(self.on_render_finished)
        self.render_worker.start()
        self._preview_job = 0

        # Coalesce rapid slider events instead of rendering for every pixel move.
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(90)
        self._render_timer.timeout.connect(self.render)

        central = QWidget()
        self.setCentralWidget(central)
        outer = QHBoxLayout(central)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(splitter)

        preview_panel = QWidget()
        preview_layout = QVBoxLayout(preview_panel)
        header = QHBoxLayout()
        header.setSpacing(10)
        logo = QLabel()
        logo.setObjectName("BrandLogo")
        logo.setPixmap(brand_pixmap(24))
        logo.setFixedSize(26, 26)
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(logo)
        title = QLabel(APP_TITLE)
        title.setProperty("role", "title")
        header.addWidget(title)
        header.addStretch()
        self.status_label = QLabel("就绪 · 自动检测 GPU")
        self.status_label.setProperty("role", "status")
        header.addWidget(self.status_label)
        preview_layout.addLayout(header)

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
        right_side.addWidget(self.after_canvas)
        depth_panel = QWidget()
        depth_layout = QVBoxLayout(depth_panel)
        depth_layout.setContentsMargins(0, 6, 0, 0)
        depth_layout.setSpacing(4)
        depth_title = QLabel("深度图预览")
        depth_title.setProperty("role", "hint")
        self.depth_preview = DepthPreview()
        depth_layout.addWidget(depth_title)
        depth_layout.addWidget(self.depth_preview, 1)
        right_side.addWidget(depth_panel)
        right_side.setCollapsible(0, False)
        right_side.setCollapsible(1, False)
        right_side.setSizes([560, 190])

        canvases.addWidget(self.before_canvas)
        canvases.addWidget(right_side)
        canvases.setSizes([470, 470])
        preview_layout.addWidget(canvases, 1)

        bottom = QHBoxLayout()
        self.open_btn = QPushButton("导入图片")
        self.open_btn.clicked.connect(self.open_image)
        set_variant(self.open_btn, "warm")
        self.compare_btn = QPushButton("显示原图")
        self.compare_btn.setCheckable(True)
        self.compare_btn.toggled.connect(self.toggle_original)
        self.export_btn = QPushButton("导出效果图")
        self.export_btn.clicked.connect(self.export_image)
        set_variant(self.export_btn, "primary")
        bottom.addWidget(self.open_btn)
        bottom.addWidget(self.compare_btn)
        bottom.addStretch()
        bottom.addWidget(self.export_btn)
        preview_layout.addLayout(bottom)
        splitter.addWidget(preview_panel)

        controls = QWidget()
        controls_layout = QVBoxLayout(controls)

        camera_box = QGroupBox("镜头与虚化")
        camera_form = QFormLayout(camera_box)
        self.aperture = QComboBox()
        self.aperture.addItems(["f/1.2", "f/1.4", "f/1.8", "f/2.0", "f/2.8", "f/4.0", "f/5.6", "f/8.0"])
        self.aperture.setCurrentIndex(1)
        self.aperture.currentIndexChanged.connect(self.schedule_render)
        camera_form.addRow("模拟光圈", self.aperture)

        self.blur_slider, self.blur_value = self.make_slider(0, 100, 55)
        self.blur_slider.valueChanged.connect(self.on_settings_changed)
        camera_form.addRow("虚化强度", self.slider_row(self.blur_slider, self.blur_value))

        self.bokeh_slider, self.bokeh_value = self.make_slider(0, 100, 35)
        self.bokeh_slider.valueChanged.connect(self.on_settings_changed)
        camera_form.addRow("光斑强度", self.slider_row(self.bokeh_slider, self.bokeh_value))

        self.bokeh_shape = QComboBox()
        self.bokeh_shape.addItem("圆形（圆叶片）", 0)
        for blades in (5, 6, 7, 8, 9):
            self.bokeh_shape.addItem(f"{blades} 边形光圈", blades)
        self.bokeh_shape.setCurrentIndex(3)
        self.bokeh_shape.currentIndexChanged.connect(self.schedule_render)
        camera_form.addRow("光斑形状", self.bokeh_shape)

        self.bokeh_density, self.bokeh_density_value = self.make_slider(0, 100, 55)
        self.bokeh_density.valueChanged.connect(self.on_settings_changed)
        camera_form.addRow("光斑密度", self.slider_row(self.bokeh_density, self.bokeh_density_value))

        self.bokeh_size, self.bokeh_size_value = self.make_slider(30, 200, 100)
        self.bokeh_size_value.setText("100%")
        self.bokeh_size.valueChanged.connect(self.on_settings_changed)
        camera_form.addRow("光斑大小", self.slider_row(self.bokeh_size, self.bokeh_size_value))

        self.spherical_slider, self.spherical_value = self.make_slider(-100, 100, 0)
        self.spherical_slider.valueChanged.connect(self.on_settings_changed)
        self.spherical_slider.setToolTip(
            "球差决定焦外光斑的亮度分布：正值＝欠校正（边缘亮环、硬朗的“肥皂泡”散景）；"
            "负值＝过校正（中心亮、柔和的奶油散景）；0＝校正良好、亮度均匀。"
        )
        self.spherical_value.setText("0")
        camera_form.addRow("球差", self.slider_row(self.spherical_slider, self.spherical_value))

        self.cat_eye_slider, self.cat_eye_value = self.make_slider(0, 100, 0)
        self.cat_eye_slider.valueChanged.connect(self.on_settings_changed)
        self.cat_eye_slider.setToolTip(
            "猫眼压缩：模拟画面边缘的机械暗角，把靠边的光斑横向压扁。"
            "0＝所有光斑都是正圆（默认）；数值越大，靠边的光斑越像橄榄形。"
        )
        self.cat_eye_value.setText("0")
        camera_form.addRow("猫眼压缩", self.slider_row(self.cat_eye_slider, self.cat_eye_value))
        self.highlight_check = QCheckBox("高光溢出补偿")
        self.highlight_check.setToolTip(
            "勾选后，比阈值更亮的光源，其散景光斑按比例降低不透明度，"
            "避免过亮处多个光斑叠加成一片纯白。"
        )
        self.highlight_check.toggled.connect(self.on_settings_changed)
        camera_form.addRow(self.highlight_check)
        self.highlight_slider, self.highlight_value = self.make_slider(0, 100, 80)
        self.highlight_slider.valueChanged.connect(self.on_settings_changed)
        self.highlight_slider.setToolTip(
            "高光阈值：亮度超过该值（0–100，对应 0–255 的亮度）才算高光，"
            "越亮的光斑压得越透明。默认 80。"
        )
        self.highlight_value.setText("80")
        camera_form.addRow(
            "高光阈值", self.slider_row(self.highlight_slider, self.highlight_value)
        )
        controls_layout.addWidget(camera_box)

        depth_box = QGroupBox("景深与主体")
        depth_form = QFormLayout(depth_box)
        self.depth_mode = QComboBox()
        self.depth_mode.addItems(["深度图对焦（AI 估算 / 导入）", "中心对焦（无需模型）", "手动焦点（点击预览）"])
        self.depth_mode.currentIndexChanged.connect(self.on_depth_mode_changed)
        depth_form.addRow("对焦模式", self.depth_mode)

        self.model_combo = QComboBox()
        depth_form.addRow("AI 模型", self.model_combo)
        self.refresh_model_labels()

        self.import_model_btn = QPushButton("导入 AI 模型")
        self.import_model_btn.setToolTip(
            "导入本地模型文件夹，或填仓库 ID / 链接；本地没有时运行 AI 估算会自动联网下载"
        )
        self.import_model_btn.clicked.connect(self.import_model)
        depth_form.addRow(self.import_model_btn)

        self.depth_size = QComboBox()
        self.depth_size.addItem("快速 · 518px（模型原生）", 518)
        self.depth_size.addItem("标准 · 756px", 756)
        self.depth_size.addItem("高 · 1036px", 1036)
        self.depth_size.addItem("更高 · 1260px", 1260)
        self.depth_size.setToolTip(
            "AI 深度估算的推理分辨率：越高，边缘与细节越细，但更慢、更吃内存。"
            "仅在下次运行 AI 估算时生效。"
        )
        self.depth_size.currentIndexChanged.connect(self.on_depth_size_changed)
        depth_form.addRow("深度图分辨率", self.depth_size)

        self.focus_slider, self.focus_value = self.make_slider(0, 100, 45)
        self.focus_slider.valueChanged.connect(self.on_settings_changed)
        depth_form.addRow("焦点深度", self.slider_row(self.focus_slider, self.focus_value))

        self.depth_strength, self.depth_strength_value = self.make_slider(0, 100, 70)
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

        self.keep_subject = QCheckBox("尽量保护清晰主体")
        self.keep_subject.setChecked(True)
        self.keep_subject.stateChanged.connect(self.on_settings_changed)
        depth_form.addRow(self.keep_subject)

        self.subject_expand_slider, self.subject_expand_value = self.make_slider(-100, 100, 0)
        self.subject_expand_slider.valueChanged.connect(self.on_settings_changed)
        self.subject_expand_slider.setToolTip(
            "主体边缘：负值收缩清晰主体（把深度误差造成的一圈边缘光晕收进去），"
            "正值扩张主体（把主体边缘多包进来一点）。0＝自动。"
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
        controls_layout.addWidget(depth_box)

        output_box = QGroupBox("预览与输出")
        output_form = QFormLayout(output_box)
        self.preview_scale = QComboBox()
        self.preview_scale.addItems(["快速预览（最长边 1000px）", "标准预览（最长边 1400px）", "高质量预览（原尺寸）"])
        self.preview_scale.currentIndexChanged.connect(self.reload_preview_size)
        output_form.addRow("预览质量", self.preview_scale)
        self.range_check = QCheckBox("显示效果范围（红色高亮被改动的区域）")
        self.range_check.setToolTip(
            "仅影响预览：把被虚化或光斑改动过的像素用半透明红色标出，"
            "方便检查效果边界；导出文件不受影响。"
        )
        self.range_check.toggled.connect(self.refresh_display)
        output_form.addRow(self.range_check)
        self.report_btn = QPushButton("导出使用报告")
        self.report_btn.setToolTip(
            "把运行日志与设备信息（CPU / 内存 / 显卡 / 驱动等）导出成一个 txt，"
            "方便发给开发者排查问题。\n"
            f"当前计算后端：{self.engine.backend_description}"
        )
        self.report_btn.clicked.connect(self.export_usage_report)
        output_form.addRow("使用报告", self.report_btn)
        self.version_label = QLabel(f"v{APP_VERSION} · {APP_EDITION}")
        self.version_label.setProperty("role", "status")
        output_form.addRow("版本", self.version_label)
        controls_layout.addWidget(output_box)

        hist_box = QGroupBox("直方图")
        hist_layout = QVBoxLayout(hist_box)
        self.histogram = HistogramPanel()
        hist_layout.addWidget(self.histogram)
        controls_layout.addWidget(hist_box)

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
        controls_layout.addWidget(log_box)
        controls_layout.addStretch()

        note = QLabel("提示：Small 已内置，Base/Large 首次使用会自动下载。也可导入现成深度图，或直接使用中心对焦。")
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
        splitter.addWidget(scroll)
        splitter.setStretchFactor(0, 1)
        splitter.setSizes([940, 320])

        self.setStatusBar(QStatusBar())
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
        # 设备档案延迟到窗口出现后再采集（要查显卡/驱动，别拖慢启动）。
        QTimer.singleShot(1200, self.log_device_profile)

    def _refresh_action_roles(self):
        """空状态禁用导出；有图后启用。按钮角色由 variant 固定，不随状态变。"""
        self.export_btn.setEnabled(self.original_full is not None)

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
        state["protect_subject"] = self.keep_subject.isChecked()
        state["image_path"] = self.image_path
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
        self.original_full = state.get("original_full")
        self._depth_full = state.get("depth_full")
        self._depth_name = state.get("depth_name")
        self.depth = state.get("depth")
        self._ai_generation += 1
        self.ai_btn.setText("运行 AI 深度估算")

        if self.original_full is None:
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
        self.on_settings_changed()

    # -- 配置记忆：下次打开继承上次设置 ------------------------------------

    def _store(self):
        return QSettings(str(settings_path()), QSettings.Format.IniFormat)

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

        self.aperture.setCurrentIndex(as_int("aperture", self.aperture.currentIndex()))
        self.blur_slider.setValue(as_int("blur", self.blur_slider.value()))
        self.bokeh_slider.setValue(as_int("bokeh", self.bokeh_slider.value()))
        self.bokeh_shape.setCurrentIndex(
            as_int("bokeh_shape", self.bokeh_shape.currentIndex())
        )
        self.bokeh_density.setValue(as_int("bokeh_density", self.bokeh_density.value()))
        self.bokeh_size.setValue(as_int("bokeh_size", self.bokeh_size.value()))
        self.spherical_slider.setValue(
            as_int("spherical", self.spherical_slider.value())
        )
        self.cat_eye_slider.setValue(as_int("cat_eye", self.cat_eye_slider.value()))
        self.focus_slider.setValue(as_int("focus", self.focus_slider.value()))
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
            as_int("highlight_threshold", self.highlight_slider.value())
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
        store.setValue("spherical", self.spherical_slider.value())
        store.setValue("cat_eye", self.cat_eye_slider.value())
        store.setValue("focus", self.focus_slider.value())
        store.setValue("depth_strength", self.depth_strength.value())
        store.setValue("protect_subject", self.keep_subject.isChecked())
        store.setValue("subject_expand", self.subject_expand_slider.value())
        store.setValue("subject_feather", self.subject_feather_slider.value())
        store.setValue("highlight_rolloff", self.highlight_check.isChecked())
        store.setValue("highlight_threshold", self.highlight_slider.value())
        store.setValue("preview_scale", self.preview_scale.currentIndex())
        store.setValue("depth_mode", self.depth_mode.currentIndex())
        store.setValue("depth_size_index", self.depth_size.currentIndex())
        store.setValue("model", self.model_combo.currentData() or "")
        store.setValue("geometry", self.saveGeometry())
        store.sync()

    def make_slider(self, minimum, maximum, value):
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(minimum, maximum)
        slider.setValue(value)
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
        for name, repo_id, size in MODEL_CHOICES:
            status = "已下载" if resolve_model_path(repo_id) else "首次需下载"
            self.model_combo.addItem(f"{name}（{size}，{status}）", repo_id)
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
        try:
            image = load_image_rgb(path)
        except Exception as exc:
            QMessageBox.critical(self, "打开失败", str(exc))
            return
        self.image_path = path
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

    def reload_preview_size(self, *_):
        if self.original_full is None:
            return
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
        self.update_histogram()
        self._record("切换 预览质量")
        self.render()

    def settings(self):
        f_stops = [1.2, 1.4, 1.8, 2.0, 2.8, 4.0, 5.6, 8.0]
        return EngineSettings(
            blur_strength=self.blur_slider.value() / 100.0,
            bokeh_strength=self.bokeh_slider.value() / 100.0,
            aperture=f_stops[self.aperture.currentIndex()],
            focus=self.focus_slider.value() / 100.0,
            depth_strength=self.depth_strength.value() / 100.0,
            protect_subject=self.keep_subject.isChecked(),
            bokeh_blades=int(self.bokeh_shape.currentData()),
            bokeh_density=self.bokeh_density.value() / 100.0,
            bokeh_size=self.bokeh_size.value() / 100.0,
            spherical=self.spherical_slider.value() / 100.0,
            cat_eye=self.cat_eye_slider.value() / 100.0,
            subject_expand=self.subject_expand_slider.value() / 100.0,
            subject_feather=self.subject_feather_slider.value() / 100.0,
            highlight_rolloff=self.highlight_check.isChecked(),
            highlight_threshold=self.highlight_slider.value() / 100.0,
        )

    # -- 预览显示（效果范围高亮 / 直方图） ----------------------------------

    def _effect_mask(self):
        """被效果改动过的像素遮罩（只用于预览，不参与导出）。"""
        if self.processed is None or self.original is None:
            return None
        if self.processed.shape[:2] != self.original.shape[:2]:
            return None
        diff = np.abs(
            self.processed.astype(np.int16) - self.original.astype(np.int16)
        ).max(axis=2)
        return diff > 3

    def _display_image(self):
        """效果预览显示的图：可选叠加半透明红色标出效果范围。"""
        base = self.original if self.compare_btn.isChecked() else self.processed
        if base is None or not self.range_check.isChecked():
            return base
        mask = self._effect_mask()
        if mask is None or not mask.any():
            return base
        tinted = base.astype(np.float32)  # copy：不污染 self.processed
        tinted[mask] = tinted[mask] * 0.5 + np.array([255.0, 40.0, 40.0]) * 0.5
        return tinted.astype(np.uint8)

    def refresh_display(self, *_):
        """刷新效果预览（保持缩放/平移）并同步直方图。"""
        self.after_canvas.set_image(self._display_image(), keep_view=True)
        self.update_histogram()

    def update_histogram(self, *_):
        self.histogram.set_image(
            self.processed if self.processed is not None else self.original
        )

    # -- 日志与使用报告 ------------------------------------------------------

    def _device_profile(self):
        profile = collect_device_profile()
        profile["qt"] = QT_VERSION
        profile["backend"] = self.engine.backend_description
        return profile

    def log_device_profile(self):
        """启动后记一次设备档案；与上次不同（换了机器）时记完整信息。"""
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
            self.event_log.read_all() or "（本次还没有日志）",
        ]
        target = self.event_log.directory / f"使用报告-{time.strftime('%Y%m%d-%H%M%S')}.txt"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError as exc:
            self.event_log.log("report.error", error=str(exc))
            QMessageBox.critical(self, "导出失败", f"写不了报告文件：\n{exc}")
            return
        self.event_log.log("report.export", path=str(target))
        QMessageBox.information(
            self, "使用报告已导出",
            f"报告已保存到：\n{target}\n\n把它发给开发者即可（含设备信息与处理日志）。"
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
            label.setText(str(slider.value()))
        self.bokeh_size_value.setText(f"{self.bokeh_size.value()}%")
        for slider, label in (
            (self.spherical_slider, self.spherical_value),
            (self.subject_expand_slider, self.subject_expand_value),
        ):
            value = slider.value()
            label.setText(f"+{value}" if value > 0 else str(value))
        self._record(self._control_label())
        self.schedule_render()

    def schedule_render(self, *_):
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
            self.statusBar().showMessage("点击预览中的主体位置，即可把焦点设到该处。", 6000)
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
        self.focus_slider.setValue(int(round(focus * 100)))
        self.focus_slider.blockSignals(False)
        self.focus_value.setText(str(self.focus_slider.value()))
        self.statusBar().showMessage(f"焦点深度已设为 {self.focus_slider.value()}（点击位置）", 3000)
        self._record("点击设置焦点", immediate=True)
        self.render()

    def toggle_original(self, checked):
        # 切换对比保持缩放/平移（keep_view）：放大看细节时视图不会跳回全图。
        self.refresh_display()

    def render(self, *_):
        self._render_timer.stop()
        if self.original is None:
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
        timeout.start(60000)
        try:
            self.render_worker.submit(
                image, depth, settings, mode,
                kind=kind, return_layers=return_layers, tag=token,
            )
            loop.exec()
        finally:
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

    def ai_progress(self, text, worker):
        if worker is not self.depth_worker:
            return
        self.ai_hint.setText(text)
        self.statusBar().showMessage(text)

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
        self.focus_slider.setValue(int(round(focus * 100)))
        self.focus_slider.blockSignals(False)
        self.focus_value.setText(str(self.focus_slider.value()))

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
                "还没有深度图。请先运行 AI 深度估算，或导入一张深度图。"
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
                "没有写入权限：\n" + str(path) + "\n\n"
                "请选择“下载”、项目文件夹或其他可写目录。\n"
                "如果程序运行在 DSH/沙箱环境中，只能写入项目目录。\n\n"
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
        self.focus_slider.setValue(int(round(focus * 100)))
        self.focus_slider.blockSignals(False)
        self.focus_value.setText(str(self.focus_slider.value()))

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
                note = "\n\n透明图层已包含虚化/光斑，叠在原图上方即可还原。"
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
            QMessageBox.information(
                self, "导出完成",
                f"已导出：\n{path}\n尺寸：{out_w}×{out_h}{note}"
            )
            # 导出完成后再唤起资源管理器，避免对话框挡住新开的窗口。
            if not reveal_in_explorer(path):
                self.statusBar().showMessage("已导出，但未能自动打开文件夹。", 6000)
        except PermissionError as exc:
            self.event_log.log("export.error", kind="permission", path=str(path))
            QMessageBox.critical(
                self, "导出失败",
                "没有写入权限：\n" + str(path) + "\n\n"
                "请选择“下载”、项目文件夹或其他可写目录。\n"
                "如果程序运行在 DSH/沙箱环境中，只能写入项目目录。\n\n"
                + str(exc)
            )
        except Exception as exc:
            self.event_log.log("export.error", error=str(exc))
            QMessageBox.critical(self, "导出失败", str(exc))

    def closeEvent(self, event):
        worker = self.depth_worker
        if worker is not None and worker.isRunning():
            self.statusBar().showMessage("正在结束 AI 任务，请稍候…")
            QApplication.processEvents()
            worker.requestInterruption()
            if not worker.wait(5000):
                worker.terminate()
                worker.wait(1000)
        self.depth_worker = None
        self.render_worker.stop()
        self._save_config()
        self.event_log.log("app.exit", actions=len(self.history))
        if self._render_timer.isActive():
            self._render_timer.stop()
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
