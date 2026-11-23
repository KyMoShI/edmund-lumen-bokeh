"""Frameless main window chrome, with native Qt move/resize where available."""
from PySide6.QtCore import QObject, QEvent, QPoint, QRect, Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QToolButton, QWidget


class WindowTitleBar(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self._drag = None
        self.setObjectName("MainWindowTitleBar")
        self.setFixedHeight(34)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 6, 0)
        layout.setSpacing(0)
        self.caption = QLabel(window.windowTitle(), self)
        self.caption.setTextFormat(Qt.TextFormat.PlainText)
        self.caption.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.caption.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.caption, 1)
        self.minimize = self._button("—", "最小化", window.showMinimized)
        self.maximize = self._button("□", "最大化", self.toggle_maximized)
        self.close_button = self._button("×", "关闭", window.close)
        self.close_button.setObjectName("MainWindowCloseButton")
        self.setStyleSheet("""
            #MainWindowTitleBar { background: palette(window); }
            #MainWindowTitleBar QLabel { background: transparent; color: palette(window-text); font-size: 12px; }
            #MainWindowTitleBar QToolButton { background: transparent; border: none; border-radius: 3px;
                color: palette(window-text); font-size: 17px; padding: 0px; }
            #MainWindowTitleBar QToolButton:hover { background: rgba(128,128,128,35); }
            #MainWindowTitleBar #MainWindowCloseButton:hover { background: #e44740; color: white; }
        """)
        window.windowTitleChanged.connect(self.update_caption)

    def _button(self, text, label, callback):
        button = QToolButton(self)
        button.setText(text)
        button.setToolTip(label)
        button.setAccessibleName(label)
        button.setFixedSize(38, 28)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.clicked.connect(callback)
        self.layout().addWidget(button)
        return button

    def update_caption(self, title):
        self.caption.setToolTip(title)
        self.caption.setText(self.caption.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight,
                                                                   max(1, self.caption.width())))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_caption(self.window.windowTitle())

    def toggle_maximized(self):
        self.window.showNormal() if self.window.isMaximized() else self.window.showMaximized()

    def sync_state(self):
        maximized = self.window.isMaximized()
        self.maximize.setText("❐" if maximized else "□")
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
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        window.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.title_bar = WindowTitleBar(window)
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
        normal = not (self.window.isMaximized() or self.window.isFullScreen())
        for widget, rect in zip(self.edges, rects):
            widget.setGeometry(*rect)
            widget.setVisible(normal)
            widget.raise_()
        self.title_bar.sync_state()

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.WindowStateChange, QEvent.Type.Show):
            self.layout_edges()
        return False
