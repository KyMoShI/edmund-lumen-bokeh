"""Frameless main window chrome, with native Qt move/resize where available."""
import sys
from PySide6.QtCore import QObject, QEvent, QPoint, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QRegion
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QSizePolicy, QToolButton, QWidget
from theme.tokens import RADIUS


class WindowDot(QToolButton):
    """A 12px design dot within a larger, keyboard-accessible hit target."""
    def __init__(self, color, label, parent):
        super().__init__(parent)
        self.color = QColor(color)
        self.setToolTip(label)
        self.setAccessibleName(label)
        self.setFixedSize(24, 28)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        diameter = 12
        circle = QRectF((self.width()-diameter)/2, (self.height()-diameter)/2, diameter, diameter)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.state_color(self.underMouse(), self.hasFocus(), self.isDown()))
        painter.drawEllipse(circle)

    def state_color(self, hovered=False, focused=False, pressed=False):
        color = self.color
        # Dark-theme visibility is also a fill change, never an outline.
        if color == QColor("#000000") and self.palette().window().color().lightness() < 96:
            color = QColor("#606060")
        gray_mix = .50 if pressed else .28 if hovered or focused else 0
        return QColor(*(round(channel*(1-gray_mix) + 160*gray_mix)
                        for channel in (color.red(),color.green(),color.blue())))


class WindowsFrame:
    """Retain Windows' resize/snap styles, while painting all chrome in Qt."""
    def __init__(self, window):
        import ctypes
        from ctypes import wintypes
        self.ctypes, self.types, self.window = ctypes, wintypes, window
        self.hwnd = None
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.get_style = self.user32.GetWindowLongPtrW
        self.get_style.argtypes = [wintypes.HWND, ctypes.c_int]
        self.get_style.restype = ctypes.c_ssize_t
        self.set_style = self.user32.SetWindowLongPtrW
        self.set_style.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        self.set_style.restype = ctypes.c_ssize_t
        self.user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                           ctypes.c_int, ctypes.c_int, wintypes.UINT]
        self.user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self.user32.IsZoomed.argtypes = [wintypes.HWND]
        self.user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        self.user32.MonitorFromWindow.restype = wintypes.HMONITOR
        self.user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.c_void_p]
        class MonitorInfo(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
        self.MonitorInfo = MonitorInfo

    def enable(self):
        hwnd = int(self.window.winId())
        self.hwnd = hwnd
        # WS_CAPTION is retained for shell recognition, but NCCALCSIZE below
        # removes its visual frame. These flags make Aero Snap a native operation.
        required = 0x00C00000 | 0x00040000 | 0x00080000 | 0x00020000 | 0x00010000
        style = self.get_style(hwnd, -16)
        if style & required != required:
            self.set_style(hwnd, -16, style | required)
            self.user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0020 | 0x0001 | 0x0002 | 0x0004 | 0x0010)

    def handle(self, message, frame):
        msg = self.types.MSG.from_address(int(message))
        if self.hwnd is None or msg.hWnd != self.hwnd:
            return None
        if msg.message == 0x0083 and msg.wParam:  # WM_NCCALCSIZE
            if self.user32.IsZoomed(msg.hWnd):
                info = self.MonitorInfo()
                info.cbSize = self.ctypes.sizeof(info)
                monitor = self.user32.MonitorFromWindow(msg.hWnd, 2)
                if self.user32.GetMonitorInfoW(monitor, self.ctypes.byref(info)):
                    rect = self.types.RECT.from_address(msg.lParam)
                    rect.left, rect.top, rect.right, rect.bottom = (info.rcWork.left, info.rcWork.top,
                                                                   info.rcWork.right, info.rcWork.bottom)
            return 0
        if msg.message != 0x0084:  # WM_NCHITTEST
            return None
        rect = self.types.RECT()
        if not self.user32.GetWindowRect(msg.hWnd, self.ctypes.byref(rect)):
            return None
        x = self.ctypes.c_short(msg.lParam & 0xffff).value - rect.left
        y = self.ctypes.c_short((msg.lParam >> 16) & 0xffff).value - rect.top
        scale = self.window.devicePixelRatioF()
        band = round(5 * scale)
        if not (self.window.isMaximized() or self.window.isFullScreen()):
            left, right = x < band, x >= rect.right-rect.left-band
            top, bottom = y < band, y >= rect.bottom-rect.top-band
            hit = 13 if top and left else 14 if top and right else 16 if bottom and left else 17 if bottom and right else \
                  10 if left else 11 if right else 12 if top else 15 if bottom else None
            if hit is not None:
                return hit
        point = QPoint(round(x/scale), round(y/scale))
        bar = frame.title_bar
        bar_origin = bar.mapTo(self.window, QPoint())
        if QRect(bar_origin, bar.size()).contains(point):
            local = point - bar_origin
            for button in (bar.minimize, bar.maximize, bar.close_button):
                if button.geometry().contains(local):
                    # All dots are client-painted Qt buttons. HTMAXBUTTON would
                    # let Windows paint its restore glyph over the custom dots.
                    # Caption/edge hit regions above still provide native Aero Snap.
                    return 1  # HTCLIENT
            return 2  # HTCAPTION: native drag, double-click and Windows edge tiling
        return None


class WindowTitleBar(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self._drag = None
        self.setObjectName("MainWindowTitleBar")
        self.setFixedHeight(34)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 0, 14, 0)
        layout.setSpacing(0)
        self.caption = QLabel(window.windowTitle(), self)
        self.caption.setTextFormat(Qt.TextFormat.PlainText)
        self.caption.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.caption.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.caption, 1)
        self.minimize = self._button("#000000", "最小化", window.showMinimized)
        self.maximize = self._button("#001EFF", "最大化", self.toggle_maximized)
        self.close_button = self._button("#FF3D36", "关闭", window.close)
        self.close_button.setObjectName("MainWindowCloseButton")
        self.setStyleSheet("""
            #MainWindowTitleBar { background: palette(window); }
            #MainWindowTitleBar QLabel { background: transparent; color: palette(window-text); font-size: 12px; }
            #MainWindowTitleBar QToolButton { background: transparent; border: none; padding: 0px; }
        """)
        window.windowTitleChanged.connect(self.update_caption)

    def _button(self, color, label, callback):
        button = WindowDot(color, label, self)
        button.clicked.connect(callback)
        self.layout().addWidget(button)
        return button

    def update_caption(self, title):
        self.caption.setToolTip(title)
        self.caption.setText(self.caption.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight,
                                                                   max(1, self.caption.width())))

    def refresh(self):
        self.update_caption(self.window.windowTitle())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_caption(self.window.windowTitle())

    def toggle_maximized(self):
        self.window.showNormal() if self.window.isMaximized() else self.window.showMaximized()

    def sync_state(self):
        maximized = self.window.isMaximized()
        label = "还原窗口" if maximized else "最大化"
        self.maximize.setToolTip(label)
        self.maximize.setAccessibleName(label)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = None
            self.toggle_maximized()
            event.accept()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        handle = self.window.windowHandle()
        if handle is not None and handle.startSystemMove():
            event.accept()
            return
        point = event.globalPosition().toPoint()
        if self.window.isMaximized():
            ratio = event.position().x() / max(1, self.width())
            normal = self.window.normalGeometry()
            self.window.showNormal()
            self.window.move(point - QPoint(round(normal.width() * ratio), round(event.position().y())))
        self._drag = point - self.window.pos()
        event.accept()

    def mouseMoveEvent(self, event):
        if self._drag is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.window.move(event.globalPosition().toPoint() - self._drag)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag = None
        super().mouseReleaseEvent(event)


class ResizeEdge(QWidget):
    def __init__(self, window, edges, cursor):
        super().__init__(window)
        self.window, self.edges = window, edges
        self.setCursor(cursor)
        self.setObjectName("WindowResizeEdge")
        self.setStyleSheet("background: transparent; border: none;")
        self._origin = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window.windowHandle()
            if handle is None or not handle.startSystemResize(self.edges):
                self._origin = (event.globalPosition().toPoint(), self.window.geometry())
            event.accept()

    def mouseMoveEvent(self, event):
        if self._origin is None:
            return
        point, original = self._origin
        delta = event.globalPosition().toPoint() - point
        rect = QRect(original)
        minimum = self.window.minimumSize().expandedTo(self.window.minimumSizeHint())
        if self.edges & Qt.Edge.LeftEdge:
            rect.setLeft(min(original.right() - minimum.width() + 1, original.left() + delta.x()))
        if self.edges & Qt.Edge.RightEdge:
            rect.setRight(max(original.left() + minimum.width() - 1, original.right() + delta.x()))
        if self.edges & Qt.Edge.TopEdge:
            rect.setTop(min(original.bottom() - minimum.height() + 1, original.top() + delta.y()))
        if self.edges & Qt.Edge.BottomEdge:
            rect.setBottom(max(original.top() + minimum.height() - 1, original.bottom() + delta.y()))
        self.window.setGeometry(rect)
        event.accept()

    def mouseReleaseEvent(self, event):
        self._origin = None
        super().mouseReleaseEvent(event)


class FramelessWindowFrame(QObject):
    def __init__(self, window, title_bar=None, *, rounded_mask=True, edge_inset=0):
        super().__init__(window)
        self.window = window
        self.native = WindowsFrame(window) if sys.platform == "win32" and QApplication.platformName() == "windows" else None
        self.corner_radius = RADIUS["radius.lg"]
        self.rounded_mask = rounded_mask
        self.edge_inset = edge_inset
        window.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.title_bar = title_bar or WindowTitleBar(window)
        if title_bar is None:
            window.setMenuWidget(self.title_bar)
        left, right, top, bottom = Qt.Edge.LeftEdge, Qt.Edge.RightEdge, Qt.Edge.TopEdge, Qt.Edge.BottomEdge
        self.edges = [ResizeEdge(window, edge, cursor) for edge, cursor in (
            (left, Qt.CursorShape.SizeHorCursor), (right, Qt.CursorShape.SizeHorCursor),
            (top, Qt.CursorShape.SizeVerCursor), (bottom, Qt.CursorShape.SizeVerCursor),
            (top | left, Qt.CursorShape.SizeFDiagCursor), (top | right, Qt.CursorShape.SizeBDiagCursor),
            (bottom | left, Qt.CursorShape.SizeBDiagCursor), (bottom | right, Qt.CursorShape.SizeFDiagCursor))]
        window.installEventFilter(self)
        self.layout_edges()

    def layout_edges(self):
        width, height, band = self.window.width(), self.window.height(), 5
        rects = [(0, band, band, height - 2*band), (width-band, band, band, height-2*band),
                 (band, 0, width-2*band, band), (band, height-band, width-2*band, band),
                 (0, 0, band, band), (width-band, 0, band, band),
                 (0, height-band, band, band), (width-band, height-band, band, band)]
        if self.edge_inset:
            inset = self.edge_inset
            width, height = width-2*inset, height-2*inset
            rects = [(0, band, band, height-2*band), (width-band, band, band, height-2*band),
                     (band, 0, width-2*band, band), (band, height-band, width-2*band, band),
                     (0, 0, band, band), (width-band, 0, band, band),
                     (0, height-band, band, band), (width-band, height-band, band, band)]
            rects = [(x+inset,y+inset,w,h) for x,y,w,h in rects]
        normal = not (self.window.isMaximized() or self.window.isFullScreen())
        if self.rounded_mask and normal and not self._is_snapped():
            path = QPainterPath()
            path.addRoundedRect(QRectF(self.window.rect()), self.corner_radius, self.corner_radius)
            self.window.setMask(QRegion(path.toFillPolygon().toPolygon()))
        else:
            self.window.clearMask()
        for widget, rect in zip(self.edges, rects):
            widget.setGeometry(*rect)
            widget.setVisible(normal)
            widget.raise_()
        self.title_bar.sync_state()

    def _is_snapped(self):
        # Snapped windows meet the work-area edges flush, as native Windows do.
        screen = self.window.screen()
        if screen is None:
            return False
        area, rect = screen.availableGeometry(), self.window.geometry()
        if not area.adjusted(-2, -2, 2, 2).contains(rect):
            return False
        near = lambda a, b: abs(a-b) <= 2
        vertical = near(rect.top(), area.top()) and (near(rect.bottom(), area.bottom()) or near(rect.height(), area.height()//2))
        horizontal = near(rect.left(), area.left()) or near(rect.right(), area.right())
        return vertical and horizontal

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Show and self.native:
            self.native.enable()
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Move, QEvent.Type.WindowStateChange, QEvent.Type.Show):
            self.layout_edges()
        return False
