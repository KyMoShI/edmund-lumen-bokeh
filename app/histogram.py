"""Shared 8-bit image statistics and theme-independent histogram widgets.

Counts describe the RGB preview buffer before any display tint or letterbox.
The luminance entry uses the existing OpenCV encoded-RGB grayscale conversion;
it is an intensity distribution, not a linear-light radiometric measurement.
"""
from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import QObject, QPropertyAnimation, QEasingCurve, QRectF, QSize, Qt, Signal, QSignalBlocker
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QPushButton, QSizePolicy, QVBoxLayout, QWidget

from workspace_controls import control_font


CHANNELS=(('亮度','luminance'),('红色','red'),('绿色','green'),('蓝色','blue'))
CHANNEL_LABELS={key:label for label,key in CHANNELS}
HISTOGRAM_BACKGROUND='#E6E6E6'


def _empty_counts():
    counts={key:np.zeros(256,dtype=np.int64) for key in ('luminance','red','green','blue')}
    for values in counts.values():values.setflags(write=False)
    return counts


def _counts256(values):
    """Exact bins with bounded conversion buffers even for full-size previews."""
    counts=np.zeros(256,dtype=np.int64)
    rows=max(1,262144//values.shape[1])
    for top in range(0,values.shape[0],rows):
        counts+=np.bincount(values[top:top+rows].ravel(),minlength=256)
    return counts


class HistogramModel(QObject):
    """Compute all channels once per image; changing channels only redraws.

    ``counts`` always contains four read-only int64 arrays of length 256.
    An empty or invalid image produces all-zero counts, ``has_image=False`` and
    ``pixel_count=0``, while retaining the selected channel. Input is RGB uint8.
    """
    changed=Signal()
    channelChanged=Signal(str)

    def __init__(self,parent=None):
        super().__init__(parent)
        self.counts=_empty_counts()
        self.channel='luminance'
        self.has_image=False
        self.pixel_count=0
        self._image=None

    def set_image(self,image):
        # Render results are immutable buffers in the editor. Retain the most
        # recent one so compare/range refreshes do not repeatedly count it.
        if image is self._image:return
        self._image=image
        counts=None
        try:
            array=np.asarray(image) if image is not None else None
            if (array is not None and array.dtype==np.uint8 and array.ndim==3
                    and array.shape[2]>=3 and array.shape[0]>0 and array.shape[1]>0):
                rgb=np.ascontiguousarray(array[:,:,:3])
                gray=cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY)
                counts={'luminance':_counts256(gray)}
                for index,key in enumerate(('red','green','blue')):
                    counts[key]=_counts256(rgb[:,:,index])
                for key,values in counts.items():
                    counts[key]=values.astype(np.int64,copy=False)
                    counts[key].setflags(write=False)
                self.pixel_count=int(rgb.shape[0]*rgb.shape[1])
        except (TypeError,ValueError,OverflowError,cv2.error):
            counts=None
        self.has_image=counts is not None
        if self.has_image:self.counts=counts
        else:self.counts=_empty_counts();self.pixel_count=0
        self.changed.emit()

    def set_channel(self,channel):
        if channel not in CHANNEL_LABELS or channel==self.channel:return False
        self.channel=channel
        self.channelChanged.emit(channel)
        return True


class HistogramPlot(QWidget):
    """Gray field with white bars and space reserved for overlaid actions."""
    def __init__(self,parent=None,model=None,*,compact=False):
        super().__init__(parent)
        self.model=model if model is not None else HistogramModel(self)
        self.compact=compact
        self.setObjectName('HistogramPlot')
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumHeight(0 if compact else 88)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Expanding)
        self.model.changed.connect(self._refresh)
        self.model.channelChanged.connect(self._refresh)
        self._refresh()

    @property
    def _counts(self):
        # Preserve the old plot's inspection API for its selected single view.
        return self.model.counts.get(self.model.channel) if self.model.has_image else None

    def set_image(self,image):
        self.model.set_image(image)

    def set_channel(self,channel):
        return self.model.set_channel(channel)

    def _refresh(self,*_):
        self.setAccessibleName(CHANNEL_LABELS[self.model.channel]+'直方图')
        self.setAccessibleDescription('尚无图像' if not self.model.has_image else
            f'{self.model.pixel_count} 个像素；横轴强度 0 至 255，纵轴为峰值归一化后的相对频数。')
        self.update()

    def sizeHint(self):
        return QSize(256,64 if self.compact else 120)

    def paintEvent(self,event):
        painter=QPainter(self)
        painter.fillRect(self.rect(),QColor(HISTOGRAM_BACKGROUND))
        width,height=self.width(),self.height()
        if width<8 or height<8:return
        painter.setFont(control_font(8 if self.compact else 10))
        if not self.model.has_image:
            return
        # Actions occupy the first 32 logical pixels. Keeping the bar field
        # below that row prevents the selected distribution's peaks being hidden.
        top=38
        bottom=5
        if height<=top+bottom:return
        area=QRectF(5,top,max(1,width-10),max(1,height-top-bottom))
        painter.setPen(QPen(QColor(255,255,255,28),.7))
        for fraction in (.25,.5,.75):
            y=area.bottom()-area.height()*fraction
            painter.drawLine(area.left(),y,area.right(),y)
        painter.setPen(Qt.PenStyle.NoPen)
        values=self.model.counts[self.model.channel]
        peak=int(values.max())
        if peak>0:
            painter.setBrush(QColor('#FFFFFF'))
            # Aggregate when less than one device-independent pixel per
            # bin is available. Taking the maximum preserves sparse peaks.
            columns=min(256,max(1,int(area.width())))
            for column in range(columns):
                first=column*256//columns;last=max(first+1,(column+1)*256//columns)
                count=int(values[first:last].max())
                if count<=0:continue
                x0=area.left()+column*area.width()/columns
                x1=area.left()+(column+1)*area.width()/columns
                bar_height=count*area.height()/peak
                painter.drawRect(QRectF(x0,area.bottom()-bar_height,max(.5,x1-x0),bar_height))


class HistogramPanel(QWidget):
    """Shared plot with three optional color toggles over its upper edge."""
    def __init__(self,parent=None,model=None,*,compact=False,collapsible=True,
                 motion=None,animation_factory=None):
        super().__init__(parent)
        self.model=model if model is not None else HistogramModel(self)
        self.compact=compact;self.collapsible=collapsible
        self.motion=motion if motion is not None else {'enabled':True,'speed':1}
        self._expanded_height=88
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0);layout.setSpacing(0)
        self.setMinimumWidth(140 if collapsible else 102)
        if collapsible:self.setMinimumHeight(32)
        self.plot=HistogramPlot(self,self.model,compact=compact)
        self.plot.setMinimumHeight(0)
        if collapsible:self.plot.setMaximumHeight(self._expanded_height)
        layout.addWidget(self.plot,1)
        button_style='''QPushButton {background:#262626;color:#FFFFFF;border:1px solid transparent;border-radius:5px;min-width:0;min-height:0;padding:0;font-family:"Sarasa Gothic SC";font-size:12px;font-weight:700;}
            QPushButton:hover {background:#3B3B3B;}
            QPushButton:checked {background:#001EFF;color:#FFFFFF;}
            QPushButton:checked:hover {background:#001EFF;}
            QPushButton:focus {border-color:#FFFFFF;}
            QPushButton:disabled {background:#262626;color:#B0B0B0;}
            QPushButton:checked:disabled {background:#001EFF;color:#FFFFFF;}'''
        self.channel_buttons={}
        for caption,key in (('R','red'),('G','green'),('B','blue')):
            button=QPushButton(caption,self);button.setFixedSize(26,26)
            button.setObjectName('HistogramChannel_'+key);button.setCheckable(True)
            button.setFont(control_font(12,bold=True));button.setStyleSheet(button_style)
            button.setAccessibleName(CHANNEL_LABELS[key]+'分布')
            button.setAccessibleDescription('再次激活可恢复亮度分布。')
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False,key=key:self._select_channel(key))
            self.channel_buttons[key]=button
        self.toggle_btn=QPushButton('−',self);self.toggle_btn.setFixedSize(26,26)
        self.toggle_btn.setToolTip('折叠 / 展开直方图');self.toggle_btn.setAccessibleName('折叠直方图')
        self.toggle_btn.setFont(control_font(14,bold=True));self.toggle_btn.setStyleSheet(button_style)
        self.toggle_btn.setVisible(collapsible)
        self._collapsed=False
        factory=animation_factory or QPropertyAnimation
        self._uses_default_animation=animation_factory is None
        self._fold_animation=factory(self.plot,b'maximumHeight',self)
        self._fold_animation.setDuration(220)
        self._fold_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fold_animation.finished.connect(self._finish_fold)
        self.toggle_btn.clicked.connect(self.toggle_collapsed)
        self.model.changed.connect(self._sync_model)
        self.model.channelChanged.connect(self._sync_model)
        self._sync_model()
        self._position_controls()

    def _position_controls(self):
        start=max(6,self.width()-6-3*26-2*6)
        for index,button in enumerate(self.channel_buttons.values()):
            button.move(start+index*32,6)
            button.setVisible(not self._collapsed);button.raise_()
        self.toggle_btn.move(6,6);self.toggle_btn.raise_()

    def resizeEvent(self,event):
        self._position_controls();super().resizeEvent(event)

    def paintEvent(self,event):
        painter=QPainter(self);painter.fillRect(self.rect(),QColor(HISTOGRAM_BACKGROUND))

    def _sync_model(self,*_):
        for key,button in self.channel_buttons.items():
            with QSignalBlocker(button):button.setChecked(self.model.channel==key)
            button.setEnabled(self.model.has_image)

    def _select_channel(self,key):
        self.model.set_channel('luminance' if self.model.channel==key else key)

    def toggle_collapsed(self):
        if not self.collapsible:return
        self._collapsed=not self._collapsed
        self._fold_animation.stop();self.plot.show()
        self._position_controls()
        target=0 if self._collapsed else self._expanded_height
        self.toggle_btn.setText('+' if self._collapsed else '−')
        self.toggle_btn.setAccessibleName('展开直方图' if self._collapsed else '折叠直方图')
        if not self.motion.get('enabled',True):
            self.plot.setMaximumHeight(target);self._finish_fold();return
        if self._uses_default_animation:
            self._fold_animation.setDuration(max(1,round(220/max(.1,float(self.motion.get('speed',1))))))
        self._fold_animation.setStartValue(self.plot.maximumHeight())
        self._fold_animation.setEndValue(target)
        self._fold_animation.start()

    def _finish_fold(self):
        if self._collapsed:self.plot.hide()

    def set_image(self,image):
        self.model.set_image(image)

