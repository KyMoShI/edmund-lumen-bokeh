"""Small keyboard-accessible height grips for the two docked side cards."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget


class CardHeightGrip(QWidget):
    def __init__(self,page,name,title):
        super().__init__(page)
        self.page,self.name=page,name
        self._dragging=False
        self._hover=False
        self.setObjectName('CardHeightGrip_'+name)
        self.setAccessibleName('调整'+title+'高度')
        self.setToolTip('拖动调整卡片高度；双击恢复自动高度。方向键微调，Home 收短，End 自动填满。')
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet('background:transparent;')

    def enterEvent(self,event):
        self._hover=True;self.update();super().enterEvent(event)

    def leaveEvent(self,event):
        self._hover=False;self.update();super().leaveEvent(event)

    def focusInEvent(self,event):
        self.update();super().focusInEvent(event)

    def focusOutEvent(self,event):
        if self._dragging:
            self._dragging=False;self.page.save_card_heights()
        self.update();super().focusOutEvent(event)

    def hideEvent(self,event):
        if self._dragging:
            self._dragging=False;self.page.save_card_heights()
        super().hideEvent(event)

    def mousePressEvent(self,event):
        if event.button()!=Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        self._dragging=True
        self._start_y=event.globalPosition().y()
        self._start_height=self.page.panel_rects[self.name].height()
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        event.accept();self.update()

    def mouseMoveEvent(self,event):
        if not self._dragging:return super().mouseMoveEvent(event)
        self.page.resize_card(self.name,self._start_height+event.globalPosition().y()-self._start_y)
        event.accept()

    def mouseReleaseEvent(self,event):
        if self._dragging and event.button()==Qt.MouseButton.LeftButton:
            self._dragging=False;self.page.save_card_heights();self.update();event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:
            self._dragging=False;self.page.reset_card_height(self.name);event.accept();return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self,event):
        key=event.key()
        if key==Qt.Key.Key_End:
            self.page.reset_card_height(self.name)
        elif key==Qt.Key.Key_Home:
            self.page.resize_card(self.name,self.page.CARD_MIN_HEIGHTS[self.name])
            self.page.save_card_heights()
        elif key in (Qt.Key.Key_Up,Qt.Key.Key_Down):
            step=32 if event.modifiers()&Qt.KeyboardModifier.ShiftModifier else 8
            height=self.page.panel_rects[self.name].height()+(-step if key==Qt.Key.Key_Up else step)
            self.page.resize_card(self.name,height);self.page.save_card_heights()
        else:
            super().keyPressEvent(event);return
        event.accept()

    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        active=self._hover or self._dragging or self.hasFocus()
        pen=QPen(QColor('#001EFF' if active else '#CDD2E1'),2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap);painter.setPen(pen)
        painter.drawLine(self.width()//2-12,self.height()//2,self.width()//2+12,self.height()//2)
