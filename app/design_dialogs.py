"""Paper dialogs and plain-text messages shared by the desktop application.

This module intentionally styles only its own dialog subtree. It does not
replace native file pickers or the main editor's palette and interaction state.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPalette, QPen, QPixmap, QTextOption
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QMessageBox as NativeMessageBox, QPushButton, QScrollArea, QSizeGrip,
    QSizePolicy, QTextBrowser, QToolButton, QVBoxLayout, QWidget,
)

from workspace_controls import control_font
from window_frame import FramelessWindowFrame, WindowTitleBar


BLUE = '#001EFF'
INK = '#121212'

# Local QSS wins over either application theme, keeping all secondary windows
# in the approved paper/blue family without restyling their owner window.
SHELL_STYLE = '''
QDialog#DesignDialog {background:transparent;color:#121212;}
QWidget#DesignSurface,QWidget#DesignBody,QWidget#DesignHeader {background:transparent;}
QScrollArea {background:transparent;border:none;}
QScrollArea>QWidget>QWidget {background:transparent;}
QScrollBar:vertical {background:#F1F3F8;width:10px;margin:0;border:none;border-radius:5px;}
QScrollBar::handle:vertical {background:#A4AEC5;min-height:32px;border-radius:5px;}
QScrollBar::handle:vertical:hover {background:#6E82D3;}
QScrollBar:horizontal {background:#F1F3F8;height:10px;margin:0;border:none;}
QScrollBar::handle:horizontal {background:#A4AEC5;min-width:32px;border-radius:5px;}
QScrollBar::add-line,QScrollBar::sub-line {width:0;height:0;border:none;}
QScrollBar::add-page,QScrollBar::sub-page {background:transparent;}
QSizeGrip {background:transparent;width:16px;height:16px;}
'''

DIALOG_STYLE = SHELL_STYLE + '''
QWidget {font-family:"Sarasa Gothic SC";font-size:13px;color:#121212;}
QLabel {background:transparent;color:#121212;}
QLabel[role="title"] {font-size:18px;font-weight:700;}
QLabel[role="hint"],QLabel[role="status"] {color:#626B79;font-size:12px;}
QPushButton {background:#FFFFFF;color:#121212;border:0.75px solid #001EFF;
 border-radius:7px;min-height:20px;padding:6px 12px;font-weight:600;}
QPushButton:hover {background:#F1F3FF;}
QPushButton:pressed {background:#DFE5FF;}
QPushButton:focus {border:0.75px solid #001EFF;background:#EAF0FF;}
QPushButton:disabled {background:#F7F8FA;color:#8A94A3;border-color:#B7C0D3;}
QPushButton[variant="primary"],QPushButton[variant="warm"] {
 background:#262626;color:#FFFFFF;border:0.75px solid #262626;font-weight:700;}
QPushButton[variant="primary"]:hover,QPushButton[variant="warm"]:hover {background:#424242;}
QPushButton[variant="primary"]:pressed,QPushButton[variant="warm"]:pressed {background:#101010;}
QPushButton[variant="primary"]:focus,QPushButton[variant="warm"]:focus {border-color:#001EFF;}
QPushButton[variant="accent"] {background:#FFFFFF;color:#001EFF;border-color:#001EFF;}
QToolButton {background:#FFFFFF;color:#121212;border:none;border-radius:5px;}
QToolButton:hover {background:#EDF0FF;color:#001EFF;}
QToolButton:pressed {background:#DBE2FF;}
QLineEdit,QComboBox,QSpinBox,QDoubleSpinBox {
 background:#FFFFFF;color:#121212;border:0.75px solid #A5B0EA;border-radius:5px;
 min-height:20px;padding:5px 10px;selection-background-color:#DAE1FF;selection-color:#001EFF;}
QLineEdit:focus,QComboBox:focus,QSpinBox:focus,QDoubleSpinBox:focus {border-color:#001EFF;}
QLineEdit:disabled,QComboBox:disabled,QSpinBox:disabled,QDoubleSpinBox:disabled {
 background:#F6F7FA;color:#8993A3;border-color:#D2D9E6;}
QComboBox {padding-right:26px;min-height:20px;max-height:20px;}
QComboBox::drop-down {width:24px;border:none;background:transparent;}
QComboBox::down-arrow {width:9px;height:9px;}
QComboBox QAbstractItemView {background:white;color:#121212;border:1px solid #001EFF;
 selection-background-color:#E4E9FF;selection-color:#001EFF;padding:4px;}
QSpinBox::up-button,QDoubleSpinBox::up-button {width:14px;}
QSpinBox::down-button,QDoubleSpinBox::down-button {width:14px;}
QSpinBox QLineEdit,QDoubleSpinBox QLineEdit {border:none;padding:0;min-height:0;}
QCheckBox {background:transparent;spacing:8px;min-height:24px;color:#121212;}
QCheckBox::indicator {width:14px;height:14px;border:0.75px solid #001EFF;border-radius:4px;background:white;}
QCheckBox::indicator:checked {background:#001EFF;}
QCheckBox:disabled {color:#8993A3;}
QSlider {background:transparent;min-height:26px;}
QSlider::groove:horizontal {height:5px;background:#B8BECA;border:none;border-radius:2px;}
QSlider::sub-page:horizontal {background:#001EFF;border-radius:2px;}
QSlider::handle:horizontal {width:12px;margin:-6px 0;background:white;border:1px solid #001EFF;border-radius:6px;}
QSlider::sub-page:horizontal:disabled {background:#A7B3D8;}
QGroupBox {background:rgba(255,255,255,235);border:0.75px solid #CBD2EE;border-radius:10px;
 margin-top:12px;padding:22px 16px 14px 16px;font-weight:600;}
QGroupBox::title {subcontrol-origin:margin;subcontrol-position:top left;left:16px;top:0;
 padding:3px 10px;background:#333333;color:white;border-radius:7px;font-weight:700;font-size:13px;}
QTextBrowser,QPlainTextEdit,QTextEdit {background:rgba(255,255,255,242);color:#121212;
 border:0.75px solid #D2DAEF;border-radius:8px;padding:10px;selection-background-color:#DCE3FF;selection-color:#001EFF;}
QListWidget {background:#F0F2F7;color:#121212;border:0.75px solid #D2DAEF;border-radius:6px;padding:4px;}
QListWidget::item:selected {background:#DCE3FF;color:#001EFF;}
QDialogButtonBox {background:transparent;}
'''


def paper_palette():
    palette=QPalette()
    for role,color in ((QPalette.ColorRole.Window,'#FFFFFF'),
                       (QPalette.ColorRole.WindowText,INK),(QPalette.ColorRole.Text,INK),
                       (QPalette.ColorRole.Base,'#FFFFFF'),(QPalette.ColorRole.Button,'#FFFFFF'),
                       (QPalette.ColorRole.ButtonText,INK),(QPalette.ColorRole.Highlight,BLUE),
                       (QPalette.ColorRole.HighlightedText,'#FFFFFF'),(QPalette.ColorRole.Link,BLUE)):
        palette.setColor(role,QColor(color))
    palette.setColor(QPalette.ColorGroup.Disabled,QPalette.ColorRole.Text,QColor('#8993A3'))
    palette.setColor(QPalette.ColorRole.PlaceholderText,QColor('#8A94A3'))
    return palette


class _CloseButton(QToolButton):
    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.underMouse() or self.hasFocus():
            painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(QColor('#EBEFFF'))
            painter.drawRoundedRect(QRectF(self.rect()),6,6)
        painter.setPen(QPen(QColor(BLUE if self.underMouse() else '#535B6A'),1.5,
                            Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap))
        center=QPointF(self.rect().center())
        painter.drawLine(center+QPointF(-4,-4),center+QPointF(4,4))
        painter.drawLine(center+QPointF(4,-4),center+QPointF(-4,4))


class _TitleBar(QWidget):
    def __init__(self,dialog):
        super().__init__(dialog.surface);self.dialog=dialog;self._moving=False
        self.setObjectName('DesignHeader');self.setFixedHeight(44)
        layout=QHBoxLayout(self);layout.setContentsMargins(0,0,0,0);layout.setSpacing(12)
        self.caption=QLabel();self.caption.setFont(control_font(16,bold=True))
        self.caption.setTextFormat(Qt.TextFormat.PlainText)
        self.caption.setStyleSheet('QLabel {background:#303030;color:white;border-radius:14px;padding:4px 12px;font-size:16px;font-weight:700;}')
        self.caption.setFixedHeight(30);self.caption.setMinimumWidth(0)
        self.caption.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed)
        self.caption.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.caption,1)
        self.close_button=_CloseButton();self.close_button.setFixedSize(28,28)
        self.close_button.setAccessibleName('关闭窗口');self.close_button.setToolTip('关闭窗口')
        self.close_button.clicked.connect(dialog.reject);layout.addWidget(self.close_button)

    def resizeEvent(self,event):
        super().resizeEvent(event);self.refresh()

    def refresh(self):
        text=self.dialog.windowTitle()
        self.caption.setToolTip(text)
        self.caption.setText(self.caption.fontMetrics().elidedText(text,Qt.TextElideMode.ElideRight,
                                                                  max(1,self.caption.width()-24)))

    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:
            handle=self.dialog.windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept();return
            self._moving=True;self._origin=event.globalPosition();self._window_origin=self.dialog.pos()
            event.accept();return
        super().mousePressEvent(event)

    def mouseMoveEvent(self,event):
        if self._moving:
            delta=event.globalPosition()-self._origin
            self.dialog.move(self._window_origin+delta.toPoint());event.accept();return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self,event):
        self._moving=False;super().mouseReleaseEvent(event)


class _Surface(QWidget):
    def __init__(self,parent):
        super().__init__(parent);self.setObjectName('DesignSurface')
        path=Path(__file__).resolve().parent/'assets/completion/illustration.png'
        # The extractor writes a grayscale asset with its source alpha intact.
        self.illustration=QPixmap(str(path)) if path.is_file() else QPixmap()

    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        radius=0 if self.parentWidget().isMaximized() else 18
        path=QPainterPath();path.addRoundedRect(QRectF(self.rect()).adjusted(.5,.5,-.5,-.5),radius,radius)
        painter.fillPath(path,QColor('white'));painter.save();painter.setClipPath(path)
        painter.setPen(Qt.PenStyle.NoPen)
        for row in range(8):
            for column in range(28):
                x=9+column*8+(row%2)*4;y=8+row*8
                if x>224-row*15:continue
                painter.setBrush(QColor(0,30,255,max(5,50-column-row*3)))
                painter.drawEllipse(QPointF(x,y),.75,.75)
        if not self.illustration.isNull():
            width=min(420.,self.width()*.85)
            height=width*self.illustration.height()/self.illustration.width()
            painter.setOpacity(.5);painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(QRectF(self.width()-width,self.height()-height,width,height),
                               self.illustration,QRectF(self.illustration.rect()));painter.setOpacity(1)
        painter.restore()
        painter.setPen(QPen(QColor(BLUE),.75));painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        painter.setPen(Qt.PenStyle.NoPen)
        if not getattr(self.parentWidget(),'_floating_chrome',False):
            for offset,color in ((70,'#000000'),(56,BLUE),(42,'#FF3D36')):
                painter.setBrush(QColor(color));painter.drawEllipse(QPointF(self.width()-offset,14),4.2,4.2)


class DesignDialog(QDialog):
    """A movable, resizable paper window; put the caller's layout on ``body``.

    Body layout margins are owned by the caller. The reserved chrome is 72 px
    horizontally and 120 px vertically; the helpers include that reservation.
    Long content receives scroll access when a screen cannot fit the full body.
    """
    content_insets=(36,88,36,32)

    def __init__(self,parent=None,title='',flags=None,*,workspace_panel=False,content_titled=False):
        if flags is None and isinstance(title,Qt.WindowType):flags,title=title,''
        kind=Qt.WindowType.Dialog if flags is None else flags
        self._floating_chrome = kind == Qt.WindowType.Window
        super().__init__(parent,kind|Qt.WindowType.FramelessWindowHint)
        self.setObjectName('DesignDialog');self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setPalette(paper_palette());self.setFont(control_font(13))
        # A detached card keeps its form metrics when it moves into the paper shell.
        self.setStyleSheet(SHELL_STYLE if workspace_panel else DIALOG_STYLE)
        outer=QVBoxLayout(self);outer.setContentsMargins(12,12,12,12);outer.setSpacing(0)
        self.surface=_Surface(self);outer.addWidget(self.surface)
        shell=QVBoxLayout(self.surface);shell.setContentsMargins(24,20,24,20);shell.setSpacing(12)
        self._shell=shell;self.footer=None
        self.header=WindowTitleBar(self) if self._floating_chrome else _TitleBar(self)
        if self._floating_chrome:
            self.header.setFixedHeight(44)
            self.header.caption.setFont(control_font(16,bold=True))
            self.header.caption.setStyleSheet('background:#303030;color:white;border-radius:14px;padding:4px 12px;font-size:16px;font-weight:700;')
            self.header.setStyleSheet('QWidget#MainWindowTitleBar {background:transparent;}')
            self.header.caption.setFixedHeight(30)
            if workspace_panel or content_titled:
                self.header.caption.hide()
                self.header.layout().insertStretch(0,1)
        shell.addWidget(self.header)
        self.body_scroll=QScrollArea(self.surface);self.body_scroll.setWidgetResizable(True)
        self.body_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.body_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.body_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.body=QWidget();self.body.setObjectName('DesignBody');self.body.setAutoFillBackground(False)
        self.body_scroll.setWidget(self.body);shell.addWidget(self.body_scroll,1)
        self.size_grip=QSizeGrip(self);self.size_grip.setFixedSize(16,16)
        self.size_grip.setToolTip('拖动调整窗口大小');self.size_grip.setAccessibleName('调整窗口大小')
        self._first_show=True
        self.setMinimumSize(360,220);self.resize(540,440)
        self.setWindowTitle(title)
        if self._floating_chrome:
            self.window_frame=FramelessWindowFrame(self,self.header,native_rounding=False,edge_inset=12)

    def nativeEvent(self,event_type,message):
        frame=getattr(self,'window_frame',None)
        if frame is not None and frame.native is not None:
            result=frame.native.handle(message,frame)
            if result is not None:return True,result
        return super().nativeEvent(event_type,message)

    def changeEvent(self,event):
        super().changeEvent(event)
        if getattr(self,'_floating_chrome',False) and hasattr(self,'surface'):
            margin=0 if self.isMaximized() else 12
            self.layout().setContentsMargins(margin,margin,margin,margin)
            self.surface.update()

    def set_footer(self,widget):
        """Keep dialog actions visible while the body scrolls independently."""
        if self.footer is not None:
            self._shell.removeWidget(self.footer);self.footer.hide()
        self.footer=widget
        self._shell.addWidget(widget)
        widget.show()

    @classmethod
    def minimum_width_for_body(cls,width):
        return int(width)+cls.content_insets[0]+cls.content_insets[2]

    @classmethod
    def minimum_height_for_body(cls,height):
        return int(height)+cls.content_insets[1]+cls.content_insets[3]

    minimumWidthForBody=minimum_width_for_body
    minimumHeightForBody=minimum_height_for_body

    def setWindowTitle(self,title):
        super().setWindowTitle(title)
        if hasattr(self,'header'):self.header.refresh()

    def showEvent(self,event):
        super().showEvent(event)
        if self._first_show:
            self._first_show=False
            self.adapt_to_screen()
            self.header.refresh()
            current=self.focusWidget()
            if current is None or not self.body.isAncestorOf(current):
                for widget in self.body.findChildren(QWidget):
                    if (widget.isEnabled() and widget.isVisibleTo(self.body)
                            and widget.focusPolicy()&Qt.FocusPolicy.TabFocus):
                        widget.setFocus(Qt.FocusReason.OtherFocusReason);break

    def adapt_to_screen(self):
        app=QApplication.instance();screen=self.screen() or (app.primaryScreen() if app else None)
        if screen is None:return
        bounds=screen.availableGeometry()
        # Offscreen QA often renders windows larger than its artificial screen.
        if app is not None and app.platformName()=='offscreen' and self.parentWidget() is not None:
            bounds=self.parentWidget().window().frameGeometry()
        max_width,max_height=max(280,bounds.width()-32),max(200,bounds.height()-32)
        self.resize(max(self.minimumWidth(),min(self.width(),max_width)),
                    max(self.minimumHeight(),min(self.height(),max_height)))
        parent=self.parentWidget()
        center=parent.window().frameGeometry().center() if parent is not None else bounds.center()
        self.move(center.x()-self.width()//2,center.y()-self.height()//2)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'size_grip'):
            self.size_grip.move(self.width()-30,self.height()-30);self.size_grip.raise_()

    def paintEvent(self,event):
        # Direct painting avoids nested graphics-effect caches when a card with
        # its own hover effect moves into this dialog's body.
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect=QRectF(self.rect()).adjusted(12,15,-12,-9)
        painter.setPen(Qt.PenStyle.NoPen)
        for spread in range(10,0,-1):
            painter.setBrush(QColor(0,0,0,3 if spread>4 else 5))
            painter.drawRoundedRect(rect.adjusted(-spread,-spread,spread,spread),16+spread,16+spread)


_BUTTON_NAMES={
    NativeMessageBox.StandardButton.Ok:'确定',NativeMessageBox.StandardButton.Cancel:'取消',
    NativeMessageBox.StandardButton.Close:'关闭',NativeMessageBox.StandardButton.Yes:'是',
    NativeMessageBox.StandardButton.No:'否',NativeMessageBox.StandardButton.Retry:'重试',
    NativeMessageBox.StandardButton.Abort:'终止',NativeMessageBox.StandardButton.Ignore:'忽略',
    NativeMessageBox.StandardButton.Save:'保存',NativeMessageBox.StandardButton.Discard:'放弃',
    NativeMessageBox.StandardButton.Apply:'应用',NativeMessageBox.StandardButton.Reset:'重置',
    NativeMessageBox.StandardButton.RestoreDefaults:'恢复默认值',
    NativeMessageBox.StandardButton.YesToAll:'全部是',NativeMessageBox.StandardButton.NoToAll:'全部否',
}


class MessageDialog(DesignDialog):
    """Plain, selectable, scrollable message content with QMessageBox results."""
    def __init__(self,parent,title,text,*,severity='information',
                 buttons=NativeMessageBox.StandardButton.Ok,
                 default_button=NativeMessageBox.StandardButton.NoButton):
        super().__init__(parent,title)
        self.setMinimumWidth(440);self.resize(540,360)
        self.selected_button=NativeMessageBox.StandardButton.NoButton
        self.standard_buttons=NativeMessageBox.StandardButton(getattr(buttons,'value',buttons))
        layout=QVBoxLayout(self.body);layout.setContentsMargins(0,0,0,0);layout.setSpacing(14)
        labels={'information':'提示','warning':'警告','critical':'错误','question':'确认'}
        severity_label=QLabel(labels.get(severity,'提示'))
        severity_label.setFont(control_font(12,bold=True))
        severity_color='#D7352D' if severity in ('critical','warning') else BLUE
        severity_label.setStyleSheet(f'color:{severity_color};background:transparent;font-size:12px;font-weight:700;')
        layout.addWidget(severity_label)
        self.message=QTextBrowser(self.body);self.message.setPlainText(str(text))
        self.message.setOpenExternalLinks(False);self.message.setOpenLinks(False)
        self.message.document().setDefaultTextOption(QTextOption(Qt.AlignmentFlag.AlignLeft))
        option=self.message.document().defaultTextOption();option.setWrapMode(QTextOption.WrapMode.WrapAnywhere)
        self.message.document().setDefaultTextOption(option)
        self.message.setMinimumHeight(96);self.message.setAccessibleName(str(text))
        layout.addWidget(self.message,1)
        self.buttons=QDialogButtonBox(QDialogButtonBox.StandardButton(self.standard_buttons.value))
        self.set_footer(self.buttons)
        for standard,name in _BUTTON_NAMES.items():
            button=self.buttons.button(QDialogButtonBox.StandardButton(standard.value))
            if button is not None:
                button.setText(name);button.setMinimumHeight(32)
                if standard in (NativeMessageBox.StandardButton.Ok,NativeMessageBox.StandardButton.Yes,
                                NativeMessageBox.StandardButton.Save,NativeMessageBox.StandardButton.Retry):
                    button.setProperty('variant','primary')
                    button.style().unpolish(button);button.style().polish(button);button.update()
        if default_button==NativeMessageBox.StandardButton.NoButton:
            default_button=next((value for value in (NativeMessageBox.StandardButton.Ok,NativeMessageBox.StandardButton.No,
                                                   NativeMessageBox.StandardButton.Cancel,NativeMessageBox.StandardButton.Yes)
                                 if self.standard_buttons&value),NativeMessageBox.StandardButton.NoButton)
        button=self.buttons.button(QDialogButtonBox.StandardButton(default_button.value))
        if button is not None:button.setDefault(True);button.setFocus()
        self.buttons.clicked.connect(self._choose)

    def _choose(self,button):
        self.selected_button=NativeMessageBox.StandardButton(self.buttons.standardButton(button).value)
        self.done(self.selected_button.value)

    def reject(self):
        self.selected_button=next((value for value in (NativeMessageBox.StandardButton.Cancel,NativeMessageBox.StandardButton.Close,
                                                       NativeMessageBox.StandardButton.No,NativeMessageBox.StandardButton.Ok)
                                   if self.standard_buttons&value),NativeMessageBox.StandardButton.NoButton)
        self.done(self.selected_button.value)


class DesignMessageBox(NativeMessageBox):
    """Drop-in static convenience methods; native button enums stay intact."""
    @classmethod
    def _show(cls,parent,title,text,severity,buttons,defaultButton):
        dialog=MessageDialog(parent,title,text,severity=severity,buttons=buttons,default_button=defaultButton)
        dialog.exec()
        result=dialog.selected_button;dialog.deleteLater()
        return result

    @classmethod
    def information(cls,parent,title,text,buttons=NativeMessageBox.StandardButton.Ok,
                    defaultButton=NativeMessageBox.StandardButton.NoButton):
        return cls._show(parent,title,text,'information',buttons,defaultButton)

    @classmethod
    def warning(cls,parent,title,text,buttons=NativeMessageBox.StandardButton.Ok,
                defaultButton=NativeMessageBox.StandardButton.NoButton):
        return cls._show(parent,title,text,'warning',buttons,defaultButton)

    @classmethod
    def critical(cls,parent,title,text,buttons=NativeMessageBox.StandardButton.Ok,
                 defaultButton=NativeMessageBox.StandardButton.NoButton):
        return cls._show(parent,title,text,'critical',buttons,defaultButton)

    @classmethod
    def question(cls,parent,title,text,buttons=NativeMessageBox.StandardButton.Yes|NativeMessageBox.StandardButton.No,
                 defaultButton=NativeMessageBox.StandardButton.NoButton):
        return cls._show(parent,title,text,'question',buttons,defaultButton)


def create_message_dialog(parent,title,text,**kwargs):
    return MessageDialog(parent,title,text,**kwargs)
