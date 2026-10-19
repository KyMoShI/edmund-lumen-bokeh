import os
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton, QFileDialog,
    QVBoxLayout, QHBoxLayout, QFormLayout, QSlider, QComboBox, QCheckBox,
    QMessageBox, QSplitter, QGroupBox, QStatusBar, QDialog, QDialogButtonBox
)

from engine import BokehEngine, EngineSettings, MODE_AI, MODE_MANUAL
from depth_worker import (
    DEFAULT_MODEL, MODEL_CHOICES, DepthWorker, model_label, resolve_model_path,
)
from theme import apply_theme, asset

APP_TITLE = "伯爵大光圈模拟"
BRAND_LOGO = asset("brand", "logo-mark.png")
APP_ICON = asset("brand", "app.ico")


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


class ImageCanvas(QLabel):
    clicked = Signal(float, float)

    def __init__(self):
        super().__init__("打开一张图片开始")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(360, 240)
        self.setObjectName("PreviewSurface")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pixmap = None
        self._display_size = None

    def set_image(self, image):
        if image is None:
            self._pixmap = None
            self._display_size = None
            self.setText("打开一张图片开始")
            return
        h, w, ch = image.shape
        fmt = QImage.Format.Format_RGB888
        qimg = QImage(image.data, w, h, ch * w, fmt).copy()
        self._pixmap = QPixmap.fromImage(qimg)
        self._refresh()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self):
        if not self._pixmap:
            return
        dpr = self.devicePixelRatioF()
        target_w = max(1, int(self.width() * dpr))
        target_h = max(1, int(self.height() * dpr))
        scaled = self._pixmap.scaled(
            target_w, target_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        scaled.setDevicePixelRatio(dpr)
        self.setPixmap(scaled)
        self._display_size = (scaled.width() / dpr, scaled.height() / dpr)

    def mousePressEvent(self, event):
        if (self._pixmap is not None and self._display_size is not None
                and event.button() == Qt.MouseButton.LeftButton):
            dw, dh = self._display_size
            ox = (self.width() - dw) / 2.0
            oy = (self.height() - dh) / 2.0
            x = (event.position().x() - ox) / max(dw, 1.0)
            y = (event.position().y() - oy) / max(dh, 1.0)
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


class DepthPreview(QLabel):
    """Small always-visible preview of the current depth map."""

    def __init__(self):
        super().__init__("尚无深度图")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(110)
        self.setObjectName("PreviewSurface")
        self._pixmap = None

    def set_depth(self, depth):
        if depth is None:
            self._pixmap = None
            self.setPixmap(QPixmap())
            self.setText("尚无深度图")
            return
        normalised = np.clip(np.asarray(depth, dtype=np.float32), 0.0, 1.0)
        gray = np.ascontiguousarray((normalised * 255.0).astype(np.uint8))
        h, w = gray.shape
        image = QImage(gray.data, w, h, w, QImage.Format.Format_Grayscale8).copy()
        self._pixmap = QPixmap.fromImage(image)
        self.setText("")
        self._refresh()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self):
        if self._pixmap is None:
            return
        dpr = self.devicePixelRatioF()
        scaled = self._pixmap.scaled(
            max(1, int(self.width() * dpr)),
            max(1, int(self.height() * dpr)),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        scaled.setDevicePixelRatio(dpr)
        self.setPixmap(scaled)


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


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1280, 800)
        self.engine = BokehEngine()
        self.original = None
        self.original_full = None
        self.processed = None
        self.depth = None
        self._depth_full = None
        self._depth_name = None
        self.depth_worker = None
        self._ai_generation = 0
        self.image_path = None

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
        self.before_canvas.setToolTip("原图预览")
        self.after_canvas.setToolTip("效果预览（点击可设置焦点）")
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
        controls.setMinimumWidth(285)
        controls.setMaximumWidth(350)
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
        controls_layout.addWidget(depth_box)

        output_box = QGroupBox("预览与输出")
        output_form = QFormLayout(output_box)
        self.preview_scale = QComboBox()
        self.preview_scale.addItems(["快速预览（最长边 1000px）", "标准预览（最长边 1400px）", "高质量预览（原尺寸）"])
        self.preview_scale.currentIndexChanged.connect(self.reload_preview_size)
        output_form.addRow("预览质量", self.preview_scale)
        self.gpu_label = QLabel(self.engine.backend_description)
        self.gpu_label.setWordWrap(True)
        self.gpu_label.setProperty("role", "status")
        output_form.addRow("计算后端", self.gpu_label)
        controls_layout.addWidget(output_box)
        controls_layout.addStretch()

        note = QLabel("提示：Small 已内置，Base/Large 首次使用会自动下载。也可导入现成深度图，或直接使用中心对焦。")
        note.setWordWrap(True)
        note.setProperty("role", "hint")
        controls_layout.addWidget(note)

        splitter.addWidget(controls)
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

    def _refresh_action_roles(self):
        """空状态禁用导出；有图后启用。按钮角色由 variant 固定，不随状态变。"""
        self.export_btn.setEnabled(self.original_full is not None)

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
        if current:
            index = self.model_combo.findData(current)
            if index >= 0:
                self.model_combo.setCurrentIndex(index)
        self.model_combo.blockSignals(False)

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
        self.depth_preview.set_depth(None)
        self.compare_btn.setChecked(False)
        self.reload_preview_size()
        self.setWindowTitle(f"{APP_TITLE} · {Path(path).name}")
        self._refresh_action_roles()
        self.statusBar().showMessage(
            f"已打开：{Path(path).name}（{image.shape[1]}×{image.shape[0]}）", 4000
        )

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
        )

    def on_settings_changed(self, *_):
        for slider, label in [
            (self.blur_slider, self.blur_value), (self.bokeh_slider, self.bokeh_value),
            (self.bokeh_density, self.bokeh_density_value),
            (self.bokeh_size, self.bokeh_size_value),
            (self.focus_slider, self.focus_value),
            (self.depth_strength, self.depth_strength_value)
        ]:
            label.setText(str(slider.value()))
        self.bokeh_size_value.setText(f"{self.bokeh_size.value()}%")
        self.schedule_render()

    def schedule_render(self, *_):
        self._render_timer.start()

    def on_depth_mode_changed(self, *_):
        mode = self.depth_mode.currentIndex()
        if mode == MODE_AI and self.original is not None and self.depth is None:
            self.statusBar().showMessage("深度图尚未生成或导入：当前预览使用中心对焦。", 6000)
        elif mode == MODE_MANUAL:
            self.statusBar().showMessage("点击预览中的主体位置，即可把焦点设到该处。", 6000)
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
        self.render()

    def toggle_original(self, checked):
        if self.original is None:
            self.after_canvas.set_image(None)
            return
        if checked:
            self.after_canvas.set_image(self.original)
        else:
            self.after_canvas.set_image(self.processed if self.processed is not None else self.original)

    def render(self, *_):
        self._render_timer.stop()
        if self.original is None:
            return
        try:
            mode = self.depth_mode.currentIndex()
            depth = self.depth if mode in (MODE_AI, MODE_MANUAL) and self.depth is not None else None
            self.processed = self.engine.process(self.original, depth, self.settings(), mode=mode)
            self.after_canvas.set_image(self.original if self.compare_btn.isChecked() else self.processed)
            self.status_label.setText(self.engine.backend_description)
        except Exception as exc:
            self.statusBar().showMessage(f"渲染失败：{exc}", 6000)

    def run_ai_depth(self):
        if self.original is None:
            QMessageBox.information(self, "提示", "请先打开一张图片。")
            return
        if self.depth_worker is not None and self.depth_worker.isRunning():
            return

        self._ai_generation += 1
        model_id = self.model_combo.currentData() or DEFAULT_MODEL
        worker = DepthWorker(self.original.copy(), generation=self._ai_generation, model_id=model_id)
        self.depth_worker = worker
        worker.finished_depth.connect(lambda depth, w=worker: self.ai_finished(depth, w))
        worker.failed.connect(lambda message, w=worker: self.ai_failed(message, w))
        worker.progress.connect(lambda text, w=worker: self.ai_progress(text, w))

        self.ai_btn.setEnabled(False)
        self.ai_btn.setText("正在估算深度…")
        self.model_combo.setEnabled(False)
        self.import_depth_btn.setEnabled(False)
        self.ai_hint.setText(f"正在准备模型：{model_label(model_id)}…")
        self.statusBar().showMessage("正在估算深度，请稍候…")
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
            QMessageBox.information(
                self, "导出完成",
                f"已导出{bits}深度图：\n{path}\n分辨率：{w}×{h}"
            )
        except PermissionError as exc:
            QMessageBox.critical(
                self, "导出失败",
                "没有写入权限：\n" + str(path) + "\n\n"
                "请选择“下载”、项目文件夹或其他可写目录。\n"
                "如果程序运行在 DSH/沙箱环境中，只能写入项目目录。\n\n"
                + str(exc)
            )
        except Exception as exc:
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
        self.import_depth_btn.setEnabled(True)
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
        self.statusBar().showMessage("AI 深度估算完成。", 5000)
        self.render()

    def ai_failed(self, message, worker):
        if worker is not self.depth_worker:
            return
        self.depth_worker = None
        self.ai_btn.setEnabled(True)
        self.ai_btn.setText("运行 AI 深度估算")
        self.model_combo.setEnabled(True)
        self.import_depth_btn.setEnabled(True)
        self.refresh_model_labels()
        if worker.generation != self._ai_generation:
            self.ai_hint.setText("图片或预览尺寸已变化，本次估算结果已丢弃。")
            return
        if self.depth is not None:
            self.ai_hint.setText("本次估算失败，继续使用上一次的深度图。")
        else:
            self.ai_hint.setText("未生成深度图，继续使用中心对焦。")
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

            self.statusBar().showMessage(f"正在导出 {out_w}×{out_h}…", 0)
            QApplication.processEvents()

            if layer:
                _final, effect, alpha = self.engine.process(
                    work_image, depth, settings, mode=mode, return_layers=True
                )
                save_rgba_png(path, effect, alpha)
                note = "\n\n透明图层已包含虚化/光斑，叠在原图上方即可还原。"
            else:
                out = self.engine.process(
                    work_image, depth, settings, mode=mode
                )
                save_image_file(
                    path, out, fmt, quality=int(options["quality"])
                )
                note = ""

            if mode in (MODE_AI, MODE_MANUAL) and depth is None:
                note += "\n\n注意：未生成深度图，本次使用中心对焦导出。"
            self.statusBar().showMessage("导出完成", 4000)
            QMessageBox.information(
                self, "导出完成",
                f"已导出：\n{path}\n尺寸：{out_w}×{out_h}{note}"
            )
        except PermissionError as exc:
            QMessageBox.critical(
                self, "导出失败",
                "没有写入权限：\n" + str(path) + "\n\n"
                "请选择“下载”、项目文件夹或其他可写目录。\n"
                "如果程序运行在 DSH/沙箱环境中，只能写入项目目录。\n\n"
                + str(exc)
            )
        except Exception as exc:
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
        # --windowed 打包后 stdout 是 None，直接 print 会抛异常
        stream = sys.stdout or sys.stderr
        if stream is not None:
            stream.write(f"selftest ok: {APP_TITLE}\n")
            stream.flush()
        sys.exit(0)
    sys.exit(app.exec())
