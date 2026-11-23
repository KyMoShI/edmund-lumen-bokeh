"""Source-artwork export completion over the current, blurred workspace.

The modal changes no image, parameter or history state. Navigation remains a
decision for its owner; locating the saved file is an explicit link action.
"""
from __future__ import annotations

import html
import json
from pathlib import Path
import random
import time

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap, QTextOption
from PySide6.QtWidgets import (QAbstractButton, QDialog, QFrame, QGraphicsBlurEffect,
    QGraphicsDropShadowEffect, QGraphicsOpacityEffect, QGraphicsScene, QLabel,
    QTextBrowser, QTextEdit, QWidget)

from theme import asset
from workspace_controls import control_font


def _blurred_snapshot(owner):
    source=owner.grab()
    if source.isNull():return source
    ratio=source.devicePixelRatioF()
    result=QPixmap(source.size());result.setDevicePixelRatio(ratio);result.fill(Qt.GlobalColor.white)
    scene=QGraphicsScene();item=scene.addPixmap(source)
    blur=QGraphicsBlurEffect();blur.setBlurRadius(12)
    blur.setBlurHints(QGraphicsBlurEffect.BlurHint.QualityHint);item.setGraphicsEffect(blur)
    bounds=item.boundingRect();scene.setSceneRect(bounds)
    painter=QPainter(result)
    scene.render(painter,QRectF(0,0,source.width()/ratio,source.height()/ratio),bounds)
    painter.end();scene.clear()
    return result


class _SourceButton(QAbstractButton):
    def __init__(self,panel,name):
        super().__init__(panel)
        self.panel,self.name=panel,name
        self.level=self.target=0.0
        self.setText(name.upper());self.setAccessibleName(name.upper())
        self.setToolTip('返回欢迎页' if name=='exit' else '返回工作区并保留参数')
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(lambda:self.panel.dialog.choose(name))

    def body_rect(self):
        return self.panel.map_source(self.panel.dialog.data['buttons'][self.name]['rect'],bottom=True).translated(-self.x(),-self.y())

    def hitButton(self,point):
        path=QPainterPath();radius=50*self.panel.dialog.scale
        path.addRoundedRect(self.body_rect(),radius,radius)
        return path.contains(QPointF(point))

    def enterEvent(self,event):
        self.target=1.0;self.panel.dialog.animate();super().enterEvent(event)

    def leaveEvent(self,event):
        self.target=0.0;self.panel.dialog.animate();super().leaveEvent(event)

    def focusInEvent(self,event):
        self.update();super().focusInEvent(event)

    def focusOutEvent(self,event):
        self.update();super().focusOutEvent(event)

    def keyPressEvent(self,event):
        if event.key() in (Qt.Key.Key_Enter,Qt.Key.Key_Return):
            if not event.isAutoRepeat():self.click()
            event.accept();return
        super().keyPressEvent(event)

    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(0,1 if self.isDown() else -1.5*self.level)
        data=self.panel.dialog.layers[self.name]
        target=self.panel.map_source(data['rect'],bottom=True).translated(-self.x(),-self.y())
        image=self.panel.dialog.images[self.name]
        painter.drawPixmap(target,image,QRectF(image.rect()))
        if self.hasFocus():
            painter.setPen(QPen(QColor('#001EFF'),1.1));painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(self.body_rect().adjusted(-2,-2,2,2),50*self.panel.dialog.scale+2,50*self.panel.dialog.scale+2)


class _CompletionPanel(QWidget):
    def __init__(self,dialog,parent):
        super().__init__(parent);self.dialog=dialog
        self.number=QLabel(str(random.randrange(10)),self)
        self.number.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.number.setAccessibleName('导出序号')
        self.path_title=QLabel('导出文件地址',self)
        self.path_browser=QTextBrowser(self)
        self.path_browser.setObjectName('CompletionPath')
        self.path_browser.setFrameShape(QFrame.Shape.NoFrame)
        self.path_browser.setOpenLinks(False);self.path_browser.setOpenExternalLinks(False)
        self.path_browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.path_browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.path_browser.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.path_browser.setWordWrapMode(QTextOption.WrapMode.WrapAnywhere)
        self.path_browser.document().setDocumentMargin(0)
        self.path_browser.setAccessibleName('导出文件完整路径，激活后定位文件')
        self.path_browser.setAccessibleDescription(dialog.path)
        self.path_browser.setToolTip('点击文件地址可在文件管理器中定位；路径文字支持选择和复制。')
        link=html.escape(QUrl.fromLocalFile(dialog.path).toString(),quote=True)
        self.path_browser.setHtml(f'<a href="{link}" style="color:#414141;text-decoration:none">{html.escape(dialog.path)}</a>')
        self.path_browser.anchorClicked.connect(lambda _url:dialog.fileRequested.emit(dialog.path))
        self.note=QTextBrowser(self);self.note.setObjectName('CompletionNote')
        self.note.setFrameShape(QFrame.Shape.NoFrame)
        self.note.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.note.setPlainText(dialog.details);self.note.document().setDocumentMargin(0)
        self.note.setWordWrapMode(QTextOption.WrapMode.WrapAnywhere)
        self.note.setVisible(bool(dialog.details))
        self.buttons={name:_SourceButton(self,name) for name in ('exit','next')}
        QWidget.setTabOrder(self.path_browser,self.buttons['exit'])
        QWidget.setTabOrder(self.buttons['exit'],self.buttons['next'])
        shadow=QGraphicsDropShadowEffect(self);shadow.setBlurRadius(16);shadow.setOffset(1,4)
        shadow.setColor(QColor(0,0,0,72));self.setGraphicsEffect(shadow)

    def map_source(self,values,*,bottom=False):
        x,y,width,height=values;panel=self.dialog.data['panelRect'];scale=self.dialog.scale
        return QRectF((x-panel[0])*scale,(y-panel[1])*scale+(self.dialog.extra_height if bottom else 0),width*scale,height*scale)

    def arrange(self):
        d=self.dialog;scale=d.scale
        number_size=max(19,round(83*scale))
        self.number.setFont(control_font(number_size,bold=True))
        self.number.setStyleSheet(f'background:#909090;color:white;border:0.3px solid white;font-family:"Sarasa Gothic SC";font-size:{number_size}px;font-weight:700;')
        self.number.setGeometry(self.map_source(d.data['numberRect']).toAlignedRect())
        size=max(12,round(43*scale))
        self.path_title.setFont(control_font(size));self.path_title.setStyleSheet(f'color:#414141;background:transparent;font-size:{size}px;')
        self.path_title.setGeometry(self.map_source(d.data['pathTitleRect']).toAlignedRect())
        style=f'''QTextBrowser {{background:transparent;color:#414141;border:none;padding:0;font-family:"Sarasa Gothic SC";font-size:{size}px;selection-background-color:#DCE1FF;selection-color:#001EFF;}}
            QScrollBar:vertical {{width:7px;background:transparent;}} QScrollBar::handle:vertical {{background:#A2A8BD;border-radius:3px;min-height:14px;}}
            QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {{height:0;}} QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical {{background:transparent;}}'''
        self.path_browser.setFont(control_font(size));self.path_browser.setStyleSheet(style)
        self.note.setFont(control_font(12));self.note.setStyleSheet(style.replace(f'font-size:{size}px','font-size:12px'))
        path_rect=self.map_source(d.data['pathRect']);path_rect.setHeight(d.path_height)
        self.path_browser.setGeometry(path_rect.toAlignedRect())
        if d.details:
            self.note.setGeometry(round(path_rect.x()),round(path_rect.bottom()+8),round(path_rect.width()),round(d.note_height))
        for name,button in self.buttons.items():
            button.setGeometry(self.map_source(d.data['buttons'][name]['rect'],bottom=True).adjusted(-4,-4,4,4).toAlignedRect())

    def paintEvent(self,event):
        d=self.dialog;s=d.scale
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing);p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        body=QPainterPath();body.addRoundedRect(QRectF(self.rect()).adjusted(.4,.4,-.4,-.4),50*s,50*s)
        p.fillPath(body,QColor('white'));p.save();p.setClipPath(body)
        for name,bottom in (('illustration',True),('pattern',False)):
            data=d.layers[name];image=d.images[name]
            p.drawPixmap(self.map_source(data['rect'],bottom=bottom),image,QRectF(image.rect()))
        p.restore();p.setBrush(Qt.BrushStyle.NoBrush);p.setPen(QPen(QColor('#001EFF'),max(.5,2*s)))
        p.drawPath(body)
        panel=d.data['panelRect']
        for center_x,color in ((2923.7754,'#000000'),(2964.9573,'#001EFF'),(3006.1387,'#FF3D36')):
            p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor(color))
            p.drawEllipse(QPointF((center_x-panel[0])*s,(802.38-panel[1])*s),14.249*s,14.249*s)
        title=d.layers['title'];image=d.images['title']
        p.drawPixmap(self.map_source(title['rect']),image,QRectF(image.rect()))


class ExportCompletionDialog(QDialog):
    """Modal completion; EXIT returns home, NEXT retains the current work."""
    fileRequested=Signal(str)

    def __init__(self,owner,path,*,motion=None,details=''):
        super().__init__(owner,Qt.WindowType.Dialog|Qt.WindowType.FramelessWindowHint)
        self.owner=owner;self.path=str(Path(path).resolve());self.details=str(details).strip()
        self.motion=motion if motion is not None else {'enabled':True,'interval':8,'speed':1}
        self.navigation='next';self.setWindowTitle('导出完成')
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setAccessibleName('导出完成')
        assets=asset('completion');self.data=json.loads((assets/'layout.json').read_text(encoding='utf-8'))
        self.layers={item['name']:item for item in self.data['layers']}
        self.images={name:QPixmap(str(assets/data['file'])) for name,data in self.layers.items() if name!='surface'}
        self.scale=1/3;self.extra_height=0;self.path_height=39;self.note_height=0
        self._snapshot=_blurred_snapshot(owner)
        self.foreground=QWidget(self);self.foreground.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.panel=_CompletionPanel(self,self.foreground)
        self.buttons=self.panel.buttons;self.path_browser=self.panel.path_browser
        self.number_label=self.panel.number
        self._opacity=None;self._intro=1.0;self._last_tick=time.monotonic()
        self._timer=QTimer(self);self._timer.setTimerType(Qt.TimerType.PreciseTimer);self._timer.timeout.connect(self._tick)
        owner.installEventFilter(self);self._follow_owner(refresh=False)

    @property
    def sequence_number(self):
        return int(self.number_label.text())

    @property
    def panel_rect(self):
        return QRectF(self.panel.geometry())

    def eventFilter(self,obj,event):
        if obj is self.owner and self.isVisible() and event.type() in (QEvent.Type.Move,QEvent.Type.Resize):
            self._follow_owner(refresh=event.type()==QEvent.Type.Resize)
        return False

    def _follow_owner(self,*,refresh):
        if refresh:self._snapshot=_blurred_snapshot(self.owner)
        self.setGeometry(QRect(self.owner.mapToGlobal(QPoint()),self.owner.size()))
        self._arrange();self.update()

    def _arrange(self):
        if not hasattr(self,'panel'):return
        data=self.data;source_width,source_height=data['panelRect'][2:]
        self.scale=max(.1,min(1/3,(self.width()-48)/source_width,(self.height()-48)/source_height))
        s=self.scale;width=source_width*s;base_height=source_height*s
        path_width=data['pathRect'][2]*s
        self.path_browser.setFont(control_font(max(12,round(43*s))))
        self.path_browser.document().setTextWidth(max(32,path_width-10))
        available=max(0,self.height()-48-base_height)
        base_path=data['pathRect'][3]*s
        desired=max(base_path,self.path_browser.document().size().height()+4)
        self.path_height=base_path+min(max(0,desired-base_path),min(101,available*.7 if self.details else available))
        self.note_height=0
        if self.details:
            self.panel.note.document().setTextWidth(max(32,path_width-10))
            self.note_height=min(90,max(28,self.panel.note.document().size().height()+4),max(20,available-(self.path_height-base_path)-8))
        self.extra_height=self.path_height-base_path+(self.note_height+8 if self.details else 0)
        height=base_height+self.extra_height
        center=self.data['panelCenterRatio'];x=center[0]*self.width()-width/2;y=center[1]*self.height()-height/2
        x=max(24,min(self.width()-width-24,x));y=max(24,min(self.height()-height-24,y))
        self.foreground.setGeometry(self.rect());self.panel.setGeometry(QRectF(x,y,width,height).toAlignedRect())
        self.panel.arrange()

    def resizeEvent(self,event):
        self._arrange();super().resizeEvent(event)

    def showEvent(self,event):
        self._follow_owner(refresh=False)
        self.buttons['next'].setFocus(Qt.FocusReason.OtherFocusReason)
        if self.motion.get('enabled',True):
            self._intro=0.0;self._started=time.monotonic();self._opacity=QGraphicsOpacityEffect(self.foreground)
            self._opacity.setOpacity(0);self.foreground.setGraphicsEffect(self._opacity);self.animate()
        super().showEvent(event)

    def animate(self):
        if not self.motion.get('enabled',True):
            for button in self.buttons.values():button.level=button.target;button.update()
            return
        self._last_tick=time.monotonic()
        if not self._timer.isActive():self._timer.start(max(4,int(self.motion.get('interval',8))))

    def _tick(self):
        now=time.monotonic();speed=max(.1,float(self.motion.get('speed',1)));dt=min(.08,now-self._last_tick);self._last_tick=now
        moving=False
        if self._intro<1:
            self._intro=min(1,(now-self._started)*speed/.18)
            if self._opacity is not None:self._opacity.setOpacity(1-(1-self._intro)**3)
            if self._intro==1:
                self.foreground.setGraphicsEffect(None);self._opacity=None
        for button in self.buttons.values():
            button.level+=(button.target-button.level)*min(1,dt*20*speed)
            if abs(button.target-button.level)<.005:button.level=button.target
            else:moving=True
            button.update()
        if self._intro==1 and not moving:self._timer.stop()

    def choose(self,navigation):
        self.navigation=navigation;self.done(QDialog.DialogCode.Accepted)

    def reject(self):
        self.navigation='next';self.done(QDialog.DialogCode.Rejected)

    def done(self,result):
        self._timer.stop();self.owner.removeEventFilter(self);super().done(result)

    def hideEvent(self,event):
        self._timer.stop();super().hideEvent(event)

    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.fillRect(self.rect(),Qt.GlobalColor.white)
        if not self._snapshot.isNull():p.drawPixmap(QRectF(self.rect()),self._snapshot,QRectF(self._snapshot.rect()))
        p.fillRect(self.rect(),QColor(0,0,0,29))
