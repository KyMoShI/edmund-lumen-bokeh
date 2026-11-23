"""Compact light-workspace controls bound to the existing editor state.

The original controls remain authoritative. Normal value signals synchronize
immediately; model resets and widget-state events are coalesced with one-shot
callbacks. Call ``sync_bindings(page)`` after restores that deliberately block
the original signals. There is no polling timer.
"""
from __future__ import annotations

import math
from typing import Callable

from PySide6.QtCore import QEvent, QObject, QPointF, QRect, QRectF, QSize, QSignalBlocker, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QAbstractSpinBox, QCheckBox, QComboBox, QDoubleSpinBox,
    QHBoxLayout, QLabel, QPushButton, QSlider, QSpinBox, QWidget,
    QStyle, QStyleOptionComboBox, QStylePainter, QSizePolicy,
)


BLUE = '#001EFF'
TEXT = '#121212'
FAMILY = 'Sarasa Gothic SC'


def typography_status() -> dict:
    """Report the real bundled-face fallback rather than silently using Noto."""
    styles = QFontDatabase.styles(FAMILY)
    has_semibold = any('semibold' in name.replace(' ', '').lower()
                       or 'demibold' in name.replace(' ', '').lower() for name in styles)
    return {'family': FAMILY, 'requestedWeight': 600,
            'effectiveWeight': 600 if has_semibold else 700,
            'semiboldAvailable': has_semibold}


def control_font(pixel_size: int = 12, *, bold: bool = False) -> QFont:
    font = QFont(FAMILY)
    font.setPixelSize(pixel_size)
    weight = 700 if bold else typography_status()['effectiveWeight']
    font.setWeight(QFont.Weight(weight))
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0)
    return font


class _SourceEvents(QObject):
    """Forward non-value widget state without observing repaint traffic."""
    EVENTS = {QEvent.Type.EnabledChange, QEvent.Type.ToolTipChange,
              QEvent.Type.DynamicPropertyChange, QEvent.Type.FontChange,
              QEvent.Type.StyleChange, QEvent.Type.LanguageChange}

    def __init__(self, source, target):
        super().__init__(target)
        self.target = target
        source.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() in self.EVENTS:
            self.target._queue_sync()
        return False


class _BoundMixin:
    def _bind_state(self, source):
        self.source = source
        self._syncing = False
        self._sync_timer = QTimer(self)
        self._sync_timer.setSingleShot(True)
        self._sync_timer.setInterval(0)
        self._sync_timer.timeout.connect(self.sync)
        self._source_events = _SourceEvents(source, self)
        source.destroyed.connect(self._source_destroyed)

    def _queue_sync(self, *_):
        if self.source is not None and not self._sync_timer.isActive():
            self._sync_timer.start()

    def _source_destroyed(self, *_):
        self.source = None
        self._sync_timer.stop()
        self.setEnabled(False)

    def focusInEvent(self, event):
        self.sync()
        super().focusInEvent(event)


class CompactSlider(QSlider):
    """Float-geometry slider matching the source track and vertical thumb."""
    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setObjectName('WorkspaceSlider')
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet('QSlider#WorkspaceSlider { min-height:0; background:transparent; }')
        self.setFixedHeight(25)
        self.setMinimumWidth(40)
        self._dragging = False
        self._track_left = 4.75
        self._track_width = None

    def setTrackGeometry(self, left=4.75, width=None):
        """Set float track coordinates within the integer widget geometry."""
        self._track_left, self._track_width = float(left), width
        self.update()

    def _track(self):
        width = max(1.0,self.width()-9.5) if self._track_width is None else max(1.0,self._track_width)
        return QRectF(self._track_left,self.height()/2-4.75,width,9.5)

    def _reverse(self):
        return self.invertedAppearance() != (self.layoutDirection() == Qt.LayoutDirection.RightToLeft)

    def _ratio(self):
        ratio = (self.value()-self.minimum())/max(1, self.maximum()-self.minimum())
        return 1-ratio if self._reverse() else ratio

    def _value_at(self, point):
        # Align event coordinates with reachable integer-pixel thumb centers.
        # Otherwise a click at rounded 4.75 would miss an extreme by one unit.
        track=self._track()
        left, right = round(track.left()), round(track.right())
        ratio = min(1.0, max(0.0, (point.x()-left)/max(1.0, right-left)))
        if self._reverse():
            ratio = 1-ratio
        return self.minimum()+round(ratio*(self.maximum()-self.minimum()))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = self._track()
        x = track.left()+self._ratio()*track.width()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor('#B0B0B0' if self.isEnabled() else '#D5D5D5'))
        p.drawRoundedRect(track,4.75,4.75)
        p.save()
        if self._reverse():
            p.setClipRect(QRectF(x,track.top(),max(0.0,track.right()-x),track.height()))
        else:
            p.setClipRect(QRectF(track.left(),track.top(),max(0.0,x-track.left()),track.height()))
        p.setBrush(QColor(BLUE if self.isEnabled() else '#9AA2CA'))
        p.drawRoundedRect(track,4.75,4.75)
        p.restore()
        handle = QRectF(x-4.75,self.height()/2-9.45,9.5,18.9)
        p.setBrush(QColor('#FFFFFF'))
        p.setPen(QPen(QColor(BLUE if self.isEnabled() else '#9AA2CA'),.85))
        p.drawRoundedRect(handle,4.75,4.75)
        if self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(BLUE),.8))
            p.drawRoundedRect(handle.adjusted(-2,-2,2,2),6.75,6.75)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self._dragging = True
        self.setSliderDown(True)
        self.setSliderPosition(self._value_at(event.position()))
        event.accept()

    def mouseMoveEvent(self, event):
        if self._dragging:
            self.setSliderPosition(self._value_at(event.position()))
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self.setSliderPosition(self._value_at(event.position()))
            self._dragging = False
            if not self.hasTracking():
                self.setValue(self.sliderPosition())
            self.setSliderDown(False)
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.update()


class _SignedSpinBox(QSpinBox):
    def __init__(self, signed=False, parent=None):
        super().__init__(parent)
        self.signed = signed

    def textFromValue(self, value):
        text = super().textFromValue(value)
        return '+'+text if getattr(self,'signed',False) and value>0 else text


_VALUE_STYLE = '''
QSpinBox#WorkspaceValue, QDoubleSpinBox#WorkspaceValue {
    background:transparent; color:#121212; border:0.75px solid transparent;
    border-radius:3px; padding:0 3px; min-height:0;
    font-family:"Sarasa Gothic SC"; font-size:12px;
}
QSpinBox#WorkspaceValue:focus, QDoubleSpinBox#WorkspaceValue:focus {
    border-color:#001EFF; background:#FFFFFF;
}
QSpinBox#WorkspaceValue:disabled, QDoubleSpinBox#WorkspaceValue:disabled {color:#9299A5;}
QSpinBox#WorkspaceValue::up-button,QSpinBox#WorkspaceValue::down-button,
QDoubleSpinBox#WorkspaceValue::up-button,QDoubleSpinBox#WorkspaceValue::down-button {
    width:0; height:0; border:none;
}
QSpinBox#WorkspaceValue QLineEdit,QDoubleSpinBox#WorkspaceValue QLineEdit {
    background:transparent; color:#121212; border:none; padding:0; min-height:0;
    selection-background-color:#DCE1FF;selection-color:#001EFF;
}
QSpinBox#WorkspaceValue QLineEdit:disabled,QDoubleSpinBox#WorkspaceValue QLineEdit:disabled {color:#9299A5;}
'''


class BoundSliderRow(_BoundMixin, QWidget):
    def __init__(self, label: str, original: QSlider, readout=None, parent=None,
                 *, decimals: int | None = None, suffix: str | None = None,
                 signed: bool = False, label_width: int = 66, source_layout: bool=False):
        super().__init__(parent)
        self.setObjectName('WorkspaceSliderRow')
        self._bind_state(original)
        self._source_columns=None
        self._aligned_columns=None
        self._dynamic_label_width=label_width
        self._natural_value_width=40
        self.readout_source = readout
        self.decimals = (readout.decimals() if isinstance(readout,QDoubleSpinBox) else 0) if decimals is None else decimals
        self.scale = 10**self.decimals
        self.suffix = ((readout.suffix() if isinstance(readout,(QSpinBox,QDoubleSpinBox)) else '')
                       if suffix is None else suffix)
        self.label = QLabel(label,self)
        self.label.setObjectName('WorkspaceControlLabel')
        self.label.setFont(control_font())
        self.label.setStyleSheet('QLabel#WorkspaceControlLabel {color:#121212; background:transparent;} QLabel#WorkspaceControlLabel:disabled {color:#9299A5;}')
        self.label.setFixedWidth(label_width)
        self.slider = CompactSlider(self)
        self.slider.setAccessibleName(label)
        self.label.setBuddy(self.slider)
        self.value = QDoubleSpinBox(self) if self.decimals else _SignedSpinBox(signed,self)
        self.value.setObjectName('WorkspaceValue')
        self.value.setFont(control_font())
        if isinstance(readout,(QSpinBox,QDoubleSpinBox)):
            self.value.setLocale(readout.locale())
        if self.decimals:
            self.value.setDecimals(self.decimals)
            self.value.setSingleStep(1/self.scale)
        self.value.setSuffix(self.suffix)
        self.value.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.value.setKeyboardTracking(False)
        self.value.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        self.value.setStyleSheet(_VALUE_STYLE)
        self.value.setFixedHeight(22)
        self.value.lineEdit().setTextMargins(0,0,0,0)
        self.value.setAccessibleName(label+'数值')
        self.value.setToolTip('输入数值后按 Enter 确认；方向键逐级调整。')
        layout=QHBoxLayout(self)
        self._row_layout=layout
        layout.setContentsMargins(0,0,0,0)
        layout.setSpacing(6)
        layout.addWidget(self.label)
        layout.addWidget(self.slider,1)
        layout.addWidget(self.value)
        self.setFixedHeight(30)
        self.setFocusProxy(self.slider)
        self.slider.valueChanged.connect(self._slider_changed)
        self.value.valueChanged.connect(self._number_changed)
        original.valueChanged.connect(self.sync)
        original.rangeChanged.connect(self.sync)
        self.sync()
        if source_layout:
            self.use_source_columns()

    def use_source_columns(self, *, track_offset=56.1697, track_width=139.7147,
                           value_offset=202.4189, value_width=43.4791):
        """Use measured fixed columns; values are logical px at the 1/3 basis.

        The label begins at row x=0. Track start and width are float painter
        coordinates; the number slot remains wide enough for all legal values.
        """
        self._aligned_columns=None
        self._source_columns=(float(track_offset),float(track_width),float(value_offset),float(value_width))
        self.value.setFixedHeight(22)
        self._row_layout.setEnabled(False)
        self._place_source_columns()

    def use_aligned_columns(self, *, label_width=58, gap=8, value_width=44,
                            left_margin=0, right_margin=0):
        """Align every parameter to common outer control and value columns.

        The caller provides the shared form inset. The slider's visible track
        begins at the same column as a dropdown; the number's outer
        right edge follows the form's right edge, including wider legal text.
        Both basic and supplementary parameters use the same geometry.
        """
        self._source_columns=None
        self._aligned_columns=(int(label_width),int(gap),int(value_width),
                               int(left_margin),int(right_margin))
        self.value.setFixedHeight(26)
        self._row_layout.setEnabled(False)
        self._place_source_columns()

    def use_dynamic_columns(self):
        self._source_columns=None
        self._aligned_columns=None
        self.value.setFixedHeight(22)
        self.slider.setTrackGeometry()
        self.label.setFixedWidth(self._dynamic_label_width)
        self.value.setFixedWidth(self._natural_value_width)
        self._row_layout.setEnabled(True)
        self._row_layout.invalidate()

    def _place_source_columns(self):
        if self._aligned_columns is not None:
            label_width,gap,number_width,left,right=self._aligned_columns
            value_width=max(number_width,self._natural_value_width)
            control_left=left+label_width+gap
            value_left=max(control_left+40+gap,self.width()-right-value_width)
            self.label.setFixedWidth(label_width)
            self.label.setGeometry(left,0,label_width,self.height())
            # The visible track starts on the dropdown's left alignment line;
            # the widget extends left solely to contain the extreme thumb.
            slider_left=math.floor(control_left-4.75)
            slider_right=value_left-gap
            self.slider.setGeometry(slider_left,(self.height()-25)//2,
                                    max(40,slider_right-slider_left),25)
            self.slider.setTrackGeometry(control_left-slider_left,
                                         max(1,slider_right-4.75-control_left))
            self.value.setFixedWidth(value_width)
            self.value.setGeometry(value_left,(self.height()-26)//2,value_width,26)
            return
        if self._source_columns is None:
            return
        left,width,number_left,number_width=self._source_columns
        value_width=max(math.ceil(number_width),self._natural_value_width)
        self.value.setFixedWidth(value_width)
        if self.width()>0:
            number_left=min(number_left,max(0,self.width()-value_width))
            width=min(width,max(40,number_left-6-left))
        slider_left=math.floor(left-4.75)
        slider_right=math.ceil(left+width+4.75)
        self.label.setFixedWidth(math.ceil(left))
        self.label.setGeometry(0,0,math.ceil(left),self.height())
        self.slider.setGeometry(slider_left,(self.height()-25)//2,slider_right-slider_left,25)
        self.slider.setTrackGeometry(left-slider_left,width)
        self.value.setGeometry(round(number_left),(self.height()-22)//2,value_width,22)

    def minimumSizeHint(self):
        if self._aligned_columns is not None:
            label_width,gap,number_width,left,right=self._aligned_columns
            return QSize(left+label_width+2*gap+40+
                         max(number_width,self._natural_value_width)+right,30)
        return super().minimumSizeHint()

    def resizeEvent(self,event):
        super().resizeEvent(event)
        self._place_source_columns()

    def _slider_changed(self, value):
        if not self._syncing and self.source is not None:
            self.source.setValue(value)
            self.sync()

    def _number_changed(self, value):
        if not self._syncing and self.source is not None:
            self.source.setValue(round(value*self.scale))
            self.sync()

    def sync(self, *_):
        if self.source is None or self._syncing:
            return
        self._syncing=True
        try:
            slider_block=QSignalBlocker(self.slider)
            number_block=QSignalBlocker(self.value)
            self.slider.setRange(self.source.minimum(),self.source.maximum())
            self.slider.setSingleStep(self.source.singleStep())
            self.slider.setPageStep(self.source.pageStep())
            self.slider.setTracking(self.source.hasTracking())
            self.slider.setInvertedAppearance(self.source.invertedAppearance())
            self.slider.setInvertedControls(self.source.invertedControls())
            self.slider.setValue(self.source.value())
            self.value.setRange(self.source.minimum()/self.scale,self.source.maximum()/self.scale) if self.decimals else self.value.setRange(self.source.minimum(),self.source.maximum())
            self.value.setValue(self.source.value()/self.scale if self.decimals else self.source.value())
            enabled=self.source.isEnabled()
            for widget in (self.slider,self.value,self.label): widget.setEnabled(enabled)
            self.slider.setToolTip(self.source.toolTip())
            self.label.setToolTip(self.source.toolTip())
            metrics=QFontMetrics(self.value.font())
            # All legal boundary strings, suffixes and signs share a stable slot.
            samples=[self.value.textFromValue(value)+self.value.suffix()
                     for value in (self.value.minimum(),self.value.maximum(),0)]
            self._natural_value_width=max(40,max(metrics.horizontalAdvance(text) for text in samples)+9)
            self.value.setFixedWidth(self._natural_value_width)
            self._place_source_columns()
            del slider_block,number_block
        finally:
            self._syncing=False


_COMBO_STYLE = '''
QComboBox#WorkspaceCombo {
    color:#121212; background:#FFFFFF; border:none;
    border-radius:4.75px; padding:3px 24px 3px 10px; min-height:20px;
    font-family:"Sarasa Gothic SC"; font-size:12px;
}
QComboBox#WorkspaceCombo:hover {background:#F7F8FF;}
QComboBox#WorkspaceCombo:focus {border-color:#001EFF;}
QComboBox#WorkspaceCombo:disabled {color:#9299A5;border-color:#A7AECC;}
QComboBox#WorkspaceCombo::drop-down {width:22px;border:none;background:transparent;}
QComboBox#WorkspaceCombo::down-arrow {width:9px;height:9px;}
QComboBox#WorkspaceCombo QAbstractItemView {
    background:#FFFFFF;color:#121212;border:1px solid #001EFF;
    selection-background-color:#E8EBFF;selection-color:#001EFF;
    padding:3px;font-family:"Sarasa Gothic SC";font-size:12px;
}
'''


class BoundComboBox(_BoundMixin,QComboBox):
    def __init__(self, original: QComboBox, parent=None,
                 *, text_transform: Callable[[str],str] | None=None):
        super().__init__(parent)
        self._bind_state(original)
        self._source_model=None
        self._items_state=None
        self.text_transform=text_transform or (lambda text:text)
        self.setObjectName('WorkspaceCombo')
        self.setFont(control_font())
        self.setStyleSheet(_COMBO_STYLE)
        self.setFixedHeight(26)
        self.setMinimumWidth(40)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(1)
        self.currentIndexChanged.connect(self._to_source)
        original.currentIndexChanged.connect(self.sync)
        self.sync()

    def _connect_model(self):
        model=self.source.model()
        if model is self._source_model:
            return
        self._source_model=model
        for name in ('rowsInserted','rowsRemoved','modelReset','dataChanged','layoutChanged'):
            getattr(model,name).connect(self._queue_sync)

    def _to_source(self,index):
        if not self._syncing and self.source is not None:
            self.source.setCurrentIndex(index)
            self.sync()

    def sync(self,*_):
        if self.source is None or self._syncing:
            return
        self._syncing=True
        try:
            self._connect_model()
            state=[]
            source_model=self.source.model()
            column=self.source.modelColumn()
            for index in range(self.source.count()):
                model_index=source_model.index(index,column,self.source.rootModelIndex())
                state.append((dict(source_model.itemData(model_index)),source_model.flags(model_index)))
            blocker=QSignalBlocker(self)
            if state!=self._items_state:
                self.clear()
                for index,(roles,flags) in enumerate(state):
                    self.addItem(self.text_transform(self.source.itemText(index)))
                    for role,value in roles.items():
                        if role!=int(Qt.ItemDataRole.DisplayRole):
                            self.setItemData(index,value,role)
                    if not self.source.itemData(index,Qt.ItemDataRole.ToolTipRole):
                        self.setItemData(index,self.source.itemText(index),Qt.ItemDataRole.ToolTipRole)
                    if hasattr(self.model(),'item'):
                        self.model().item(index).setFlags(flags)
                self._items_state=state
            self.setCurrentIndex(self.source.currentIndex())
            self.setEnabled(self.source.isEnabled())
            self.setToolTip(self.source.toolTip() or self.source.currentText())
            self.setAccessibleName(self.source.accessibleName() or self.source.toolTip())
            del blocker
        finally:
            self._syncing=False

    def showPopup(self):
        self.sync()
        super().showPopup()

    def paintEvent(self,event):
        option=QStyleOptionComboBox()
        self.initStyleOption(option)
        text=option.currentText
        option.currentText=''
        painter=QStylePainter(self)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox,option)
        field=self.style().subControlRect(QStyle.ComplexControl.CC_ComboBox,option,QStyle.SubControl.SC_ComboBoxEditField,self)
        painter.setFont(self.font())
        painter.setPen(QColor(TEXT if self.isEnabled() else '#9299A5'))
        display=painter.fontMetrics().elidedText(text,Qt.TextElideMode.ElideRight,max(0,field.width()))
        painter.drawText(field,Qt.AlignmentFlag.AlignCenter,display)
        painter.end()
        _paint_control_outline(self)


_CHECK_STYLE = '''
QCheckBox#WorkspaceCheck {spacing:5px;color:#121212;background:transparent;font-family:"Sarasa Gothic SC";}
QCheckBox#WorkspaceCheck::indicator {width:8px;height:8px;border:none;background:transparent;image:none;}
QCheckBox#WorkspaceCheck::indicator:checked {background:transparent;border:none;image:none;}
QCheckBox#WorkspaceCheck:disabled {color:#9299A5;}
'''


class BoundCheckBox(_BoundMixin,QCheckBox):
    def __init__(self, original: QCheckBox, text: str | None=None, parent=None):
        super().__init__(parent)
        self._bind_state(original)
        self.fixed_text=text
        self.setObjectName('WorkspaceCheck')
        self.setFont(control_font())
        self.setStyleSheet(_CHECK_STYLE)
        self.setMinimumHeight(20)
        self.stateChanged.connect(self._to_source)
        original.stateChanged.connect(self.sync)
        self.sync()

    def _to_source(self,state):
        if not self._syncing and self.source is not None:
            self.source.setCheckState(Qt.CheckState(state))
            self.sync()

    def sync(self,*_):
        if self.source is None or self._syncing:
            return
        self._syncing=True
        try:
            blocker=QSignalBlocker(self)
            self.setText(self.source.text() if self.fixed_text is None else self.fixed_text)
            self.setTristate(self.source.isTristate())
            self.setCheckState(self.source.checkState())
            self.setEnabled(self.source.isEnabled())
            self.setToolTip(self.source.toolTip())
            del blocker
        finally:
            self._syncing=False

    def paintEvent(self,event):
        super().paintEvent(event)
        from PySide6.QtWidgets import QStyle,QStyleOptionButton
        option=QStyleOptionButton();self.initStyleOption(option)
        rect=QRectF(self.style().subElementRect(QStyle.SubElement.SE_CheckBoxIndicator,option,self))
        rect=QRectF(rect.center().x()-4.085,rect.center().y()-4.085,8.17,8.17)
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        checked=self.checkState()!=Qt.CheckState.Unchecked
        ink=QColor(BLUE if checked else '#121212')
        if not self.isEnabled():ink=QColor('#9299A5')
        p.setPen(QPen(ink,.25))
        p.setBrush(ink if checked else QColor('#FFFFFF'))
        p.drawRoundedRect(rect.adjusted(.125,.125,-.125,-.125),2.097,2.097)
        if checked:
            p.setPen(QPen(QColor('#FFFFFF'),.85,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap,Qt.PenJoinStyle.RoundJoin))
            if self.checkState()==Qt.CheckState.PartiallyChecked:
                p.drawLine(QPointF(rect.left()+2,rect.center().y()),QPointF(rect.right()-2,rect.center().y()))
            else:
                from PySide6.QtGui import QPainterPath
                path=QPainterPath(QPointF(rect.left()+1.7,rect.top()+4.15))
                path.lineTo(rect.left()+3.4,rect.top()+6.05)
                path.lineTo(rect.left()+6.7,rect.top()+2.05)
                p.drawPath(path)


_BUTTON_STYLE = '''
QPushButton#WorkspaceAction {color:#121212;background:#FFFFFF;border:none;border-radius:4.75px;padding:3px 10px;min-height:0;font-family:"Sarasa Gothic SC";font-size:12px;}
QPushButton#WorkspaceAction:hover {background:#EFF1FF;}
QPushButton#WorkspaceAction:pressed {background:#DDE2FF;}
QPushButton#WorkspaceAction:focus {border-color:#001EFF;}
QPushButton#WorkspaceAction:disabled {color:#9299A5;border-color:#A7AECC;background:#FFFFFF;}
QPushButton#WorkspaceAction[solid="true"] {color:#FFFFFF;background:#262626;border-color:#262626;}
QPushButton#WorkspaceAction[solid="true"]:hover {background:#3C3C3C;}
QPushButton#WorkspaceAction[solid="true"]:pressed {background:#121212;}
QPushButton#WorkspaceAction[solid="true"]:disabled {color:#9299A5;background:#E9EAF0;}
'''


class BoundActionButton(_BoundMixin,QPushButton):
    def __init__(self,text: str,callback: Callable,parent=None,*,source:QAbstractButton|None=None,
                 solid=False,follow_text=False,pill=False,font_size=12,bold=False):
        super().__init__(text,parent)
        self.follow_text=follow_text
        self.pill=pill
        self._height_floor=26
        self.setObjectName('WorkspaceAction')
        self.setProperty('solid',solid)
        self.setFont(control_font(font_size,bold=bold))
        self.setStyleSheet(_BUTTON_STYLE.replace('font-size:12px',f'font-size:{font_size}px'))
        self.setMinimumHeight(26)
        policy=QSizePolicy(QSizePolicy.Policy.Preferred,QSizePolicy.Policy.Minimum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.setMinimumWidth(0)
        self.clicked.connect(lambda _checked=False:callback())
        if source is not None:
            self._bind_state(source)
            source.toggled.connect(self.sync)
            self.sync()
        else:
            self.source=None
        self._refresh_wrapping_height()

    def sizeHint(self):
        metrics=self.fontMetrics()
        lines=self.text().splitlines() or ['']
        return QSize(max(44,max(metrics.horizontalAdvance(line) for line in lines)+20),
                     max(26,metrics.lineSpacing()*len(lines)+6))

    def minimumSizeHint(self):
        # Narrow layouts can wrap long labels instead of clipping glyphs or
        # removing their horizontal breathing room.
        return QSize(44,max(26,self.fontMetrics().height()+6))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self,width):
        bounds=self.fontMetrics().boundingRect(QRect(0,0,max(1,width-20),100000),
                    int(Qt.AlignmentFlag.AlignCenter|Qt.TextFlag.TextWordWrap|
                        Qt.TextFlag.TextWrapAnywhere),self.text())
        return max(26,bounds.height()+6)

    def setText(self,text):
        super().setText(text)
        if hasattr(self,'pill'):
            self._refresh_wrapping_height()
        self.updateGeometry()

    def _refresh_wrapping_height(self):
        # Retain a caller's row-height floor while preventing empty containers
        # from stretching a short action into a tall slab. Long labels grow to
        # their measured wrapped height when a narrower width requires it.
        if self.minimumHeight()<self._height_floor:
            self.setMinimumHeight(self._height_floor)
        height=max(self.minimumHeight(),self.heightForWidth(self.width()))
        if self.maximumHeight()!=height:
            self.setMaximumHeight(height)

    def setFixedHeight(self,height):
        # QSS min-height is reapplied during polish; retain the explicit row
        # floor separately so that it cannot silently shrink a 28 px action.
        self._height_floor=max(26,int(height))
        super().setFixedHeight(self._height_floor)
        self._refresh_wrapping_height()

    def resizeEvent(self,event):
        super().resizeEvent(event)
        self._refresh_wrapping_height()

    def sync(self,*_):
        if self.source is None or self._syncing:
            return
        self._syncing=True
        try:
            blocker=QSignalBlocker(self)
            self.setEnabled(self.source.isEnabled())
            self.setToolTip(self.source.toolTip())
            self.setCheckable(self.source.isCheckable())
            self.setChecked(self.source.isChecked())
            if self.follow_text:
                self.setText(self.source.text())
            del blocker
        finally:
            self._syncing=False

    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        solid=bool(self.property('solid'))
        background='#262626' if solid else '#FFFFFF'
        foreground='#FFFFFF' if solid else '#121212'
        if not self.isEnabled():
            background,foreground='#E9EAF0','#9299A5'
        elif self.isDown() or self.isChecked():
            background='#121212' if solid else '#DDE2FF'
        elif self.underMouse():
            background='#3C3C3C' if solid else '#EFF1FF'
        p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor(background))
        radius=min(self.width(),self.height())/2 if self.pill else 4.75
        p.drawRoundedRect(QRectF(self.rect()),radius,radius)
        p.setPen(QColor(foreground));p.setFont(self.font())
        p.drawText(self.rect().adjusted(10,3,-10,-3),
                   Qt.AlignmentFlag.AlignCenter|Qt.TextFlag.TextWordWrap|
                   Qt.TextFlag.TextWrapAnywhere,self.text())
        p.end()
        _paint_control_outline(self,radius,solid=bool(self.property('solid')))


def _paint_control_outline(widget,radius=4.75,*,solid=False):
    """Paint the source 0.75-du border at 1/3 scale without QSS rounding."""
    painter=QPainter(widget)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    color=QColor('#262626' if solid else BLUE)
    if not widget.isEnabled():color=QColor('#A7AECC')
    painter.setPen(QPen(color,.25))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(QRectF(widget.rect()).adjusted(.125,.125,-.125,-.125),radius,radius)
    if widget.hasFocus():
        painter.setPen(QPen(QColor(BLUE),.9))
        painter.drawRoundedRect(QRectF(widget.rect()).adjusted(1,1,-1,-1),max(0,radius-1),max(0,radius-1))


def bind_combo(original:QComboBox,parent=None,**kwargs)->BoundComboBox:
    return BoundComboBox(original,parent,**kwargs)


def bind_check(original:QCheckBox,text=None,parent=None)->BoundCheckBox:
    return BoundCheckBox(original,text,parent)


def action_button(text,callback,parent=None,**kwargs)->BoundActionButton:
    return BoundActionButton(text,callback,parent,**kwargs)


def sync_bindings(container:QWidget):
    """Refresh blocked canonical changes once, without affecting history."""
    for widget in [container,*container.findChildren(QWidget)]:
        if isinstance(widget,(BoundSliderRow,BoundComboBox,BoundCheckBox,BoundActionButton)):
            widget.sync()
