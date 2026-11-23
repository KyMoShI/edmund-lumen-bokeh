"""Live light workspace anchored to the approved third artboard.

The original editor widgets remain the single state source and the dark layout.
This page binds presentation controls and shares preview pixmaps without copying
image buffers or maintaining a second processing/history pipeline.
"""
from __future__ import annotations

import json
import math
import random
from pathlib import Path
import time

from PIL import Image
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, QEvent, QEasingCurve
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPalette, QPen, QPixmap, QAction
from PySide6.QtWidgets import (
    QAbstractButton, QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QListWidget,
    QMenu, QPushButton, QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from workspace_controls import (BoundSliderRow, action_button, bind_check, bind_combo,
                                control_font, sync_bindings)
from workspace_background import WorkspaceBackground
from workspace_card_resize import CardHeightGrip
from workspace_preview_layout import PreviewLayout
from design_dialogs import DesignDialog
from histogram import HistogramPanel
from card_motion import install_card_scroll

SCALE = 1/3
FORM_INSET = 18
FORM_LABEL_WIDTH = 58
FORM_COLUMN_GAP = 8
FORM_ROW_GAP = 8
MENU_STYLE = 'QMenu {background:white;color:#121212;border:1px solid #001EFF;padding:4px;} QMenu::item:selected {background:#E8EBFF;}'


def rect(values):
    return QRectF(*(float(value)*SCALE for value in values))


class _MirrorCanvas:
    def __init__(self, source, parent):
        super().__init__()
        self.setParent(parent)
        self.source = source
        self.page=parent.page
        self._base_scale = None
        self.setMinimumSize(1, 1)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setStyleSheet('QLabel {background:#E6E6E6;border:none;border-radius:0;color:transparent;} QLabel[drag="true"] {border:2px dashed #001EFF;}')
        palette = self.palette(); palette.setColor(QPalette.ColorRole.Highlight, QColor('white')); self.setPalette(palette)
        if hasattr(self, 'clicked'):
            self.clicked.connect(source.clicked.emit)
        if hasattr(self, '_flock_timer'):
            self._flock_timer.stop()
        source.sourceChanged.connect(self.sync_source)
        self.setToolTip('滚轮缩放 · 中键拖拽平移 · 双击适应窗口 · 右键查看缩放操作')
        self.sync_source(False)

    def sync_source(self, keep_view=False):
        if not keep_view:
            self._base_scale = None
        self.set_source_pixmap(self.source._pixmap, '', keep_view=keep_view)
        self.setText('')
        if hasattr(self, '_flock_timer'):
            self._flock_timer.stop()
        self.update()

    def _fit_rect(self):
        if self._pixmap is None or self.width() < 1 or self.height() < 1:
            return None
        pw, ph = self._pixmap.width(), self._pixmap.height()
        if self._base_scale is None:
            self._base_scale = max(self.width()/pw, self.height()/ph)
        scale = self._base_scale*self._zoom
        w, h = pw*scale, ph*scale
        return QRectF((self.width()-w)/2+self._pan.x(), (self.height()-h)/2+self._pan.y(), w, h)

    def zoom_by(self,factor):
        self._zoom_at(QPointF(self.rect().center()),factor)

    def fit_to_view(self):
        if self._pixmap is None:return
        self._base_scale=min(self.width()/self._pixmap.width(),self.height()/self._pixmap.height())
        self._zoom=1.0;self._pan=QPointF();self.update()

    def actual_size(self):
        if self._pixmap is None:return
        self._base_scale=1.0;self._zoom=1.0;self._pan=QPointF();self.update()

    def reset_view(self):
        self._base_scale=None
        super().reset_view()

    def view_menu(self):
        menu=QMenu(self);menu.setStyleSheet(MENU_STYLE);menu.setFont(control_font())
        operations=(('放大','zoomIn',lambda:self.zoom_by(1.25)),
                    ('缩小','zoomOut',lambda:self.zoom_by(.8)),
                    ('适应窗口','fit',self.fit_to_view),
                    ('预览像素 1:1','actualSize',self.actual_size),
                    ('重置视图','resetView',self.reset_view))
        for text,name,callback in operations:
            action=menu.addAction(text,callback);action.setObjectName('preview.'+name)
            action.setEnabled(self._pixmap is not None)
            if name=='actualSize':action.setToolTip('按当前预览图的像素显示；采样分辨率由预览质量决定。')
        menu.addSeparator()
        action=menu.addAction('还原预览分区',lambda _checked=False:self.page.preview_layout.reset())
        action.setObjectName('preview.resetLayout')
        return menu

    def contextMenuEvent(self,event):
        menu=self.view_menu();menu.exec(event.globalPos());menu.deleteLater();event.accept()

    def wheelEvent(self,event):
        if self._pixmap is None:event.ignore();return
        delta=event.angleDelta().y()
        if delta:self._zoom_at(event.position(),1.25**(delta/120.0))
        event.accept()

    def mousePressEvent(self,event):
        if self._pixmap is not None and event.button()==Qt.MouseButton.MiddleButton:
            self._panning=True;self._pan_start=QPointF(event.position());self._pan_origin=QPointF(self._pan)
            self.setCursor(Qt.CursorShape.ClosedHandCursor);event.accept();return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self,event):
        if self._pixmap is not None and event.button()==Qt.MouseButton.LeftButton:
            self.fit_to_view();event.accept();return
        super().mouseDoubleClickEvent(event)


class _Preview(QWidget):
    def __init__(self, page, name, source):
        super().__init__(page)
        self.page, self.name = page, name
        self.setObjectName('LightPreview_'+name)
        self.canvas = type('LightCanvas_'+name, (_MirrorCanvas, type(source)), {})(source, self)
        self.tag = QLabel(self)
        self.tag.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        item=page.data['layersByName'][page.data['previews'][name]['tagLayer']]
        self.tag.setPixmap(page.images[item['name']])
        self.tag.setScaledContents(True)

    def resizeEvent(self, event):
        self.canvas.setGeometry(self.rect())
        tag=rect(self.page.data['previews'][self.name]['tagRect'])
        self.tag.setGeometry(0,0,round(tag.width()),round(tag.height()))
        super().resizeEvent(event)


class _ArtButton(QAbstractButton):
    def __init__(self, page, name, callback):
        super().__init__(page)
        self.page, self.name = page, name
        self.setText(name.upper()); self.setAccessibleName(name.upper())
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.hover = 0.0; self.target = 0.0
        self.clicked.connect(callback)

    def enterEvent(self,event):
        self.target=1.0; self.page.animate(); super().enterEvent(event)

    def leaveEvent(self,event):
        self.target=0.0; self.page.animate(); super().leaveEvent(event)

    def hitButton(self, point):
        layer=self.page.data['layersByName'][self.name]
        body=rect(self.page.data['buttons'][self.name]['rect'])
        bounds=rect(layer['rect']); body.translate(-bounds.x(),-bounds.y())
        path=QPainterPath(); path.addRoundedRect(body,50*SCALE,50*SCALE)
        return path.contains(QPointF(point))

    def keyPressEvent(self,event):
        if event.key() in (Qt.Key.Key_Return,Qt.Key.Key_Enter):
            if not event.isAutoRepeat(): self.click()
            event.accept()
        else: super().keyPressEvent(event)

    def paintEvent(self,event):
        p=QPainter(self); p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.translate(0,1 if self.isDown() else -2*self.hover)
        pixmap=self.page.images[self.name]
        p.drawPixmap(QRectF(self.rect()),pixmap,QRectF(pixmap.rect()))
        if self.hasFocus():
            layer=rect(self.page.data['layersByName'][self.name]['rect'])
            body=rect(self.page.data['buttons'][self.name]['rect']).translated(-layer.x(),-layer.y()).adjusted(-3,-3,3,3)
            p.setPen(QPen(QColor('#001EFF'),1.3));p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(body,50*SCALE+3,50*SCALE+3)


class _Metadata(QWidget):
    """Shared image histogram above the source photograph's metadata band."""
    def __init__(self,page,parent=None):
        super().__init__(parent); self.page=page; self._key=None; self.caption='';self.fields=[]
        self.setFixedHeight(113)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.histogram=HistogramPanel(self,model=page.owner.histogram.model,
                                      compact=True,collapsible=False,motion=page.motion)
        self.histogram.setAccessibleName('图像直方图')
        self._position_histogram()

    def _position_histogram(self):
        data=self.page.data['metadata'];origin=rect(data['frameRect']).topLeft()
        area=rect(data['visibleImageRect']).translated(-origin).adjusted(1,1,-1,-1)
        self.histogram.setGeometry(area.toAlignedRect())

    def resizeEvent(self,event):
        self._position_histogram();super().resizeEvent(event)

    def sync(self):
        owner=self.page.owner
        key=(owner.image_path,id(owner.original_full))
        if key!=self._key:
            self._key=key; self.caption='';self.fields=[]
            if owner.original_full is not None:
                fields=['']*4
                try:
                    with Image.open(owner.image_path) as image:
                        exif=image.getexif()
                        if 34665 in exif: exif=dict(exif.get_ifd(34665))|dict(exif)
                        for column,(tag,formatting) in enumerate([(34855,lambda x:f'ISO {x}'),(37386,lambda x:f'{float(x):g}mm'),
                                               (33437,lambda x:f'f/{float(x):g}'),
                                               (33434,lambda x:f'1/{round(1/float(x))}s' if float(x)<1 else f'{float(x):g}s')]):
                            if exif.get(tag) is not None: fields[column]=formatting(exif[tag])
                except (OSError,ValueError,TypeError,ZeroDivisionError): pass
                if not any(fields):
                    h,w=owner.original_full.shape[:2]; fields=[f'{w} × {h}',Path(owner.image_path).suffix[1:].upper()]
                self.fields=fields;self.caption='   '.join(field for field in fields if field)
        self.update()

    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        data=self.page.data['metadata']; origin=rect(data['frameRect']).topLeft()
        image_rect=rect(data['visibleImageRect']).translated(-origin)
        p.fillRect(image_rect,QColor('#E6E6E6'))
        layer=self.page.data['layersByName']['metadata_frame']
        target=rect(layer['rect']).translated(-origin)
        p.drawPixmap(target,self.page.images['metadata_frame'],QRectF(self.page.images['metadata_frame'].rect()))
        if self.caption:
            # The source periwinkle band needs a darker ink for 11 px text.
            p.setFont(control_font(11));p.setPen(QColor('#001579'))
            band=rect(data['infoBarRect']).translated(-origin)
            band.adjust(6,0,-6,0);column_width=band.width()/len(self.fields)
            for column,field in enumerate(self.fields):
                cell=QRectF(band.x()+column*column_width,band.y(),column_width,band.height())
                text=p.fontMetrics().elidedText(field,Qt.TextElideMode.ElideRight,max(1,round(cell.width()-6)))
                p.drawText(cell,Qt.AlignmentFlag.AlignCenter,text)


class _Disclosure(QLabel):
    """Vector chevrons avoid font-dependent missing triangle glyphs."""

    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor('white'),1.5,Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap,Qt.PenJoinStyle.RoundJoin))
        center=QPointF(self.rect().center())
        if self.text()=='▾':
            points=(center+QPointF(-4,-2),center+QPointF(0,2),center+QPointF(4,-2))
        else:
            points=(center+QPointF(-2,-4),center+QPointF(2,0),center+QPointF(-2,4))
        painter.drawLine(points[0],points[1]);painter.drawLine(points[1],points[2])


class _Group(QWidget):
    def __init__(self,page,title,parent=None,header_height=44,collapsible=True):
        super().__init__(parent);self.page=page
        self.collapsible=collapsible
        self.setObjectName('WorkspaceGroup')
        self.setSizePolicy(QSizePolicy.Policy.Preferred,QSizePolicy.Policy.Maximum)
        self.layout_box=QVBoxLayout(self);self.layout_box.setContentsMargins(0,0,0,0);self.layout_box.setSpacing(0)
        self.header=QWidget(self);self.header.setFixedHeight(header_height)
        self.title=QPushButton(title,self.header);self.title.setFont(control_font(15,bold=True))
        self.title.setStyleSheet('QPushButton {background:#3C3C3C;color:white;border:none;border-radius:13px;min-height:0;padding:3px 10px;} QPushButton:hover {background:#242424;}')
        self.title.setCursor(Qt.CursorShape.PointingHandCursor if collapsible else Qt.CursorShape.ArrowCursor)
        self.title.setToolTip('展开或收起本分组的扩展参数' if collapsible else '镜头与光斑参数')
        self.rule=QFrame(self.header);self.rule.setStyleSheet('background:#262626;border:none;')
        self.layout_box.addWidget(self.header)
        self.body=QWidget(self);self.body_layout=QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(FORM_INSET,0,FORM_INSET,0);self.body_layout.setSpacing(FORM_ROW_GAP)
        self.layout_box.addWidget(self.body)
        self.extra=QWidget();self.extra_layout=QVBoxLayout(self.extra)
        self.extra_layout.setContentsMargins(FORM_INSET,FORM_ROW_GAP,FORM_INSET,8);self.extra_layout.setSpacing(FORM_ROW_GAP)
        self.extra.hide()
        self._expanded=False
        self._reveal_on_finish=False
        self.title.setCheckable(collapsible)
        self.title.setFocusPolicy(Qt.FocusPolicy.StrongFocus if collapsible else Qt.FocusPolicy.NoFocus)
        self.title.setAccessibleName(title+'，展开扩展参数' if collapsible else title)
        self.disclosure=_Disclosure('▸',self.header)
        self.disclosure.setFont(control_font(12,bold=True))
        self.disclosure.setStyleSheet('color:white;background:transparent;')
        self.disclosure.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.disclosure.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.disclosure.setVisible(collapsible)
        self.animation=type(page.owner._card_tilts[0].animation)(self)
        self.animation.setDuration(220)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.valueChanged.connect(lambda value:self.extra.setMaximumHeight(round(value)))
        self.animation.finished.connect(self._finish_expansion)
        self.title.clicked.connect(self.toggle_extra)

    def toggle_extra(self):
        if not self.collapsible:return
        self._expanded=not self._expanded
        self._reveal_on_finish=self._expanded
        self.title.setChecked(self._expanded)
        self.disclosure.setText('▾' if self._expanded else '▸')
        self.title.setAccessibleName(self.title.text()+('，收起扩展参数' if self._expanded else '，展开扩展参数'))
        self.animation.stop()
        self.page._cache=None;self.page.update()
        if not self.page.motion['enabled']:
            self._finish_expansion()
            return
        start=self.extra.height() if self.extra.isVisible() else 0
        self.extra.setVisible(True)
        end=self.extra.sizeHint().height() if self._expanded else 0
        self.animation.setStartValue(float(start));self.animation.setEndValue(float(end))
        self.animation.start()

    def _finish_expansion(self):
        self.extra.setVisible(self._expanded)
        self.extra.setMaximumHeight(16777215 if self._expanded else 0)
        if self._expanded and self._reveal_on_finish:
            self._reveal_on_finish=False
            QTimer.singleShot(0,self._reveal_expanded)

    def _reveal_expanded(self):
        if not self._expanded or not self.page.content.isAncestorOf(self):return
        first=self.extra_layout.itemAt(0)
        if first is not None and first.widget() is not None:
            self.page.scroll.ensureWidgetVisible(first.widget(),0,16)
            top=self.mapTo(self.page.content,self.rect().topLeft()).y()
            self.page.scroll.verticalScrollBar().setValue(max(0,top-16))

    def finish(self):
        self.layout_box.addWidget(self.extra)

    def resizeEvent(self,event):
        width=self.width()
        self.title.setGeometry(FORM_INSET,0,max(1,width-2*FORM_INSET),26)
        self.disclosure.setGeometry(max(FORM_INSET,width-FORM_INSET-24),3,18,20)
        self.rule.setGeometry(0,34,width,1)
        super().resizeEvent(event)


class _Floating:
    def __init__(self,page,widget,title,layout=None,preview=False):
        self.page,self.widget,self.title,self.layout,self.preview=page,widget,title,layout,preview
        self.floating=None;self.placeholder=None
        self.button=QToolButton(widget);self.button.setText('↗');self.button.setFixedSize(16,16)
        self.button.setStyleSheet('QToolButton {background:white;color:#001EFF;border:1px solid #001EFF;border-radius:4px;}')
        self.button.setToolTip('在独立窗口中打开；关闭后归位');self.button.hide()
        self.button.clicked.connect(self.detach)
        widget.setMouseTracking(True)
        # Retain a QObject event filter for widget lifetime.
        from PySide6.QtCore import QObject
        class Filter(QObject):
            def eventFilter(_self,obj,event):
                if event.type()==QEvent.Type.Resize:self.position()
                if event.type()==QEvent.Type.Enter and self.floating is None:self.button.show();self.position()
                if event.type()==QEvent.Type.Leave:self.button.hide()
                return False
        self.filter=Filter(widget);widget.installEventFilter(self.filter)

    def position(self):
        self.button.move(max(0,self.widget.width()-17),3);self.button.raise_()

    def detach(self):
        if self.floating is not None:return
        self._auto_fill=self.widget.autoFillBackground()
        self.placeholder=QWidget(self.widget.parentWidget());self.placeholder.setGeometry(self.widget.geometry())
        box=QVBoxLayout(self.placeholder)
        back=action_button('恢复原位',self.restore)
        box.addWidget(back,0,Qt.AlignmentFlag.AlignCenter)
        if self.layout is not None:self.layout.replaceWidget(self.widget,self.placeholder)
        self.dialog=DesignDialog(self.page.owner,self.title,flags=Qt.WindowType.Window,workspace_panel=True)
        self.dialog.resize(540,520)
        self.dialog.setMinimumSize(self.dialog.minimum_width_for_body(max(300,self.widget.minimumWidth()+24)),
                                   self.dialog.minimum_height_for_body(196))
        target=QVBoxLayout(self.dialog.body);target.setContentsMargins(12,12,12,12)
        if self.preview:target.addWidget(self.widget)
        else:
            scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidget(self.widget);target.addWidget(scroll)
            install_card_scroll(scroll,self.page.motion)
            self.scroll=scroll
        self.floating=self.dialog;self.button.hide();self.placeholder.show()
        self.dialog.finished.connect(self.restore);self.dialog.show();self.widget.show()
        self.page.relayout();self.page.sync_motion()

    def restore(self,*_):
        if self.floating is None:return
        dialog=self.floating;self.floating=None
        if not self.preview:self.scroll.takeWidget()
        self.widget.setParent(self.placeholder.parentWidget())
        self.widget.setAutoFillBackground(self._auto_fill)
        if self.layout is not None:self.layout.replaceWidget(self.placeholder,self.widget)
        else:self.widget.setGeometry(self.placeholder.geometry())
        self.placeholder.hide();self.placeholder.deleteLater();self.placeholder=None;self.widget.show()
        self.widget.updateGeometry()
        if self.layout is not None:self.layout.invalidate();self.layout.activate()
        dialog.hide();dialog.deleteLater();self.page.relayout();self.page.sync_motion()


class WorkspacePage(QWidget):
    CARD_MIN_HEIGHTS={'history':380,'parameters':340}

    def __init__(self,owner,assets,motion):
        super().__init__(owner.pages); self.owner,self.motion=owner,motion
        self.setObjectName('LightWorkspace')
        self.setMinimumSize(900,640)
        self.data=json.loads((assets/'layout.json').read_text(encoding='utf-8'))
        self.data['layersByName']={item['name']:item for item in self.data['layers']}
        self.images={item['name']:QPixmap(str(assets/item['file'])) for item in self.data['layers'] if item['name']!='background'}
        self.background=WorkspaceBackground(assets)
        self._timer=QTimer(self);self._timer.setTimerType(Qt.TimerType.PreciseTimer);self._timer.timeout.connect(self._tick)
        self._last_tick=time.monotonic();self._cache=None;self._syncing=False
        self._history_key=None
        store=owner._store();self.card_height_ratios={}
        for name in self.CARD_MIN_HEIGHTS:
            try:ratio=float(store.value('workspace/'+name+'HeightRatio',1.0))
            except (TypeError,ValueError):ratio=1.0
            self.card_height_ratios[name]=ratio if math.isfinite(ratio) and 0<ratio<=1 else 1.0
        self.status_message=''
        self.setFont(control_font())
        self.previews={name:_Preview(self,name,source) for name,source in
                       [('original',owner.before_canvas),('effect',owner.after_canvas),('depth',owner.depth_preview)]}
        self.history_panel=QWidget(self);self.history_panel.setMinimumSize(1,self.CARD_MIN_HEIGHTS['history'])
        self.history_title=QLabel('历史记录',self.history_panel);self.history_title.setFont(control_font(15,bold=True));self.history_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.history_list=QListWidget(self.history_panel);self.history_list.setFont(control_font(11))
        self.history_list.setStyleSheet('QListWidget {background:#E6E6E6;color:#121212;border:none;padding:2px;} QListWidget::item:selected {background:#D2D9FF;color:#001EFF;}')
        self.history_list.itemClicked.connect(self._history_clicked)
        self.settings_button=action_button('设置',self._settings_menu,self.history_panel,solid=True,pill=True,font_size=15,bold=True)
        self.settings_button.setFixedHeight(30)
        self.theme_button=action_button('黑暗模式',owner.theme_btn.click,self.history_panel)
        self.motion_button=action_button('动画效果',owner.motion_btn.click,self.history_panel)
        self.help_button=action_button('帮助说明',owner.show_readme,self.history_panel)
        self.range_check=bind_check(owner.range_check,'显示效果范围',self.history_panel)
        self.range_check.setFont(control_font(10))
        self.quality_label=QLabel('预览质量',self.history_panel);self.quality_label.setFont(control_font(10,bold=True))
        self.quality=action_button('快速',self._quality_menu,self.history_panel,pill=True,font_size=8)
        self.quality.setFixedHeight(26)
        self.report_button=action_button('导出使用报告',owner.report_btn.click,self.history_panel,solid=True,pill=True,font_size=11,bold=True)
        self.report_button.setFixedHeight(28)
        for button in (self.theme_button,self.motion_button,self.help_button):button.setFixedHeight(26)
        self.history_panel.installEventFilter(self)
        self.parameters_panel=QWidget(self)
        # 267 px form + 17/8 px viewport insets + 10 px vertical scrollbar.
        # The floating dialog must not become narrower than this content.
        self.parameters_panel.setMinimumWidth(302)
        self.scroll=QScrollArea(self.parameters_panel);self.scroll.setWidgetResizable(True);self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setStyleSheet('QScrollArea {background:transparent;border:none;} QScrollArea>QWidget>QWidget {background:transparent;} QScrollBar:vertical {background:transparent;width:10px;margin:0;} QScrollBar::handle:vertical {background:#A0A0A0;border-radius:5px;min-height:40px;} QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {height:0;} QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical {background:transparent;}')
        self.content=QWidget();self.content.setFixedWidth(267)
        self.content_layout=QVBoxLayout(self.content);self.content_layout.setContentsMargins(0,0,0,0);self.content_layout.setSpacing(0)
        self.metadata=_Metadata(self);self.content_layout.addWidget(self.metadata);self.content_layout.addSpacing(34)
        self.rows={}
        self.focus=_Group(self,'焦点景深');self.content_layout.addWidget(self.focus)
        self.focus_mode=self.combo_row(self.focus,'对焦模式',owner.depth_mode,transform=lambda text:'深度图对焦' if text.startswith('深度图') else '中心对焦' if text.startswith('中心') else '点击对焦')
        self.models=self.combo_row(self.focus,'模型选择',owner.model_combo,transform=lambda text:text.split(' · ')[0].split('（')[0].strip().replace('Depth Anything V2','DA V2').replace('Depth Anything 3','DA3'))
        self.focus.extra_layout.addWidget(action_button('导入自定义模型',owner.import_model_btn.click,source=owner.import_model_btn))
        self.depth_resolution=self.combo_row(self.focus,'分辨率',owner.depth_size,extra=True,transform=lambda text:text.split(' · ')[-1].split('（')[0])
        self.depth_resolution.setAccessibleName('深度图分辨率')
        for label,name,value in [('焦点深度','focus_slider','focus_value'),('景深分离','depth_strength','depth_strength_value')]:
            self.focus.extra_layout.addWidget(self.slider_row(label,name,value))
        self.ai_button=action_button('AI 深度估算',owner.ai_btn.click,source=owner.ai_btn,solid=True,follow_text=True)
        self.focus.extra_layout.addWidget(self.ai_button)
        self.ai_hint=QLabel()
        hint_font=control_font(10);hint_font.setWeight(QFont.Weight.Normal)
        self.ai_hint.setFont(hint_font);self.ai_hint.setWordWrap(True);self.focus.extra_layout.addWidget(self.ai_hint)
        io=QWidget();io_box=QHBoxLayout(io);io_box.setContentsMargins(0,0,0,0);io_box.setSpacing(8)
        io_box.addWidget(action_button('导入深度图',owner.import_depth_btn.click,source=owner.import_depth_btn))
        io_box.addWidget(action_button('导出深度图',owner.export_depth_btn.click,source=owner.export_depth_btn));self.focus.extra_layout.addWidget(io)
        protection=bind_check(owner.keep_subject,parent=self.focus.extra);protection.setFixedHeight(26)
        self.focus.extra_layout.addWidget(protection)
        for label,name,value in [('主体边缘','subject_expand_slider','subject_expand_value'),('边缘羽化','subject_feather_slider','subject_feather_value')]:
            self.focus.extra_layout.addWidget(self.slider_row(label,name,value))
        self.focus.finish()
        self.content_layout.addSpacing(20)
        self.lens=_Group(self,'镜头虚化',collapsible=False);self.content_layout.addWidget(self.lens)
        self.aperture=self.combo_row(self.lens,'光圈',owner.aperture)
        for label,name,value in [('虚化强度','blur_slider','blur_value'),('光斑强度','bokeh_slider','bokeh_value')]:
            self.lens.body_layout.addWidget(self.slider_row(label,name,value))
        self.shape=self.combo_row(self.lens,'光斑形状',owner.bokeh_shape)
        for label,name,value in [('光斑密度','bokeh_density','bokeh_density_value'),('光斑大小','bokeh_size','bokeh_size_value')]:
            self.lens.body_layout.addWidget(self.slider_row(label,name,value))
        self.full_bokeh=bind_check(owner.full_luminance_bokeh,parent=self.lens.body)
        self.full_bokeh.setFixedHeight(26);self.lens.body_layout.addWidget(self.full_bokeh)
        for label,name,value in [('球差','spherical_slider','spherical_value'),('猫眼压缩','cat_eye_slider','cat_eye_value')]:
            self.lens.body_layout.addWidget(self.slider_row(label,name,value))
        self.highlight=bind_check(owner.highlight_check,parent=self.lens.body)
        self.highlight.setFixedHeight(26);self.lens.body_layout.addWidget(self.highlight)
        self.lens.body_layout.addWidget(self.slider_row('高光阈值','highlight_slider','highlight_value'))
        self.lens.finish()
        camera_row=QWidget();camera_row.setFixedHeight(64)
        camera_layout=QHBoxLayout(camera_row);camera_layout.setContentsMargins(FORM_INSET,8,FORM_INSET,8)
        camera_layout.addStretch();camera=QLabel()
        camera.setPixmap(self.images['camera'].scaled(59,49,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
        camera.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents);camera_layout.addWidget(camera)
        self.content_layout.addWidget(camera_row);self.content_layout.addStretch()
        self.scroll.setWidget(self.content)
        self.card_scroll=install_card_scroll(self.scroll,self.motion)
        self.scroll.verticalScrollBar().rangeChanged.connect(self._scroll_range)
        self._scroll_range(0,0)
        self.parameters_panel.installEventFilter(self)
        self.actions={name:_ArtButton(self,name,callback) for name,callback in [('import',owner.open_image),('export',owner.export_image)]}
        self.actions['import'].setToolTip('导入图片');self.actions['export'].setToolTip('导出图片')
        self.floating=[]
        for name,widget in self.previews.items():self.floating.append(_Floating(self,widget,{'original':'原图','effect':'效果图','depth':'深度图'}[name],preview=True))
        self.floating.extend([_Floating(self,self.history_panel,'历史与设置'),_Floating(self,self.parameters_panel,'参数面板'),
                              _Floating(self,self.lens,'镜头虚化',self.content_layout),_Floating(self,self.focus,'焦点景深',self.content_layout)])
        self.card_grips={name:CardHeightGrip(self,name,title) for name,title in
                         [('history','历史与设置卡片'),('parameters','参数卡片')]}
        self.preview_layout=PreviewLayout(self)
        self.relayout();self.sync()

    def slider_row(self,label,name,value):
        row=BoundSliderRow(label,getattr(self.owner,name),getattr(self.owner,value),signed=name=='subject_expand_slider',label_width=FORM_LABEL_WIDTH)
        row.use_aligned_columns(label_width=FORM_LABEL_WIDTH,gap=FORM_COLUMN_GAP,value_width=44)
        self.rows[name]=row
        return row

    def eventFilter(self,obj,event):
        if obj is self.parameters_panel and event.type()==QEvent.Type.Resize:
            self.scroll.setGeometry(17,48,max(100,obj.width()-25),max(100,obj.height()-60))
        elif obj is self.history_panel and event.type()==QEvent.Type.Resize:
            self._layout_history()
        return False

    def combo_row(self,group,label,source,extra=False,transform=None):
        host=QWidget();layout=QHBoxLayout(host);layout.setContentsMargins(0,0,0,0);layout.setSpacing(FORM_COLUMN_GAP)
        title=QLabel(label);title.setFont(control_font());title.setFixedWidth(FORM_LABEL_WIDTH)
        combo=bind_combo(source,text_transform=transform);combo.setFixedHeight(26)
        layout.addWidget(title);layout.addWidget(combo,1);host.setFixedHeight(26)
        (group.extra_layout if extra else group.body_layout).addWidget(host)
        return combo

    def _layout_history(self):
        if not hasattr(self,'report_button'):return
        left=14;width=max(90,self.history_panel.width()-28)
        self.history_title.setGeometry(left,25,width,24)
        extra=self.history_panel.height()-435
        self.history_list.setGeometry(left,54,width,max(60,125+extra))
        for widget,y,height in ((self.settings_button,193,30),(self.theme_button,233,26),
                                (self.motion_button,267,26),(self.help_button,301,26),
                                (self.range_check,337,22),(self.report_button,397,28)):
            widget.setGeometry(left,y+extra,width,height)
        self.quality_label.setGeometry(left,367+extra,width-52,26)
        self.quality.setGeometry(left+width-44,367+extra,44,26)

    def card_height_limit(self):
        top=rect(self.data['panels']['parameters']['rect']).top()
        action_top=min(rect(item['rect']).top() for item in self.data['buttons'].values())+self.height()-2160*SCALE
        return max(max(self.CARD_MIN_HEIGHTS.values()),action_top-22-top)

    def resize_card(self,name,height):
        limit=self.card_height_limit()
        bounded=max(self.CARD_MIN_HEIGHTS[name],min(limit,float(height)))
        self.card_height_ratios[name]=bounded/limit;self.relayout()

    def save_card_heights(self):
        store=self.owner._store()
        for name,ratio in self.card_height_ratios.items():store.setValue('workspace/'+name+'HeightRatio',ratio)
        store.sync()

    def reset_card_height(self,name):
        self.card_height_ratios[name]=1.0;self.relayout();self.save_card_heights()

    def _history_clicked(self,item):
        index=item.data(Qt.ItemDataRole.UserRole)
        entry=self.owner.history.goto(index)
        if entry is not None:self.owner._apply_history(*entry)

    def _scroll_range(self,minimum,maximum):
        self.scroll.verticalScrollBar().setVisible(maximum>minimum)

    def _quality_menu(self):
        menu=QMenu(self.quality)
        menu.setFont(control_font())
        menu.setStyleSheet(MENU_STYLE)
        for index,label in enumerate(('快速','标准','原图')):
            action=menu.addAction(label)
            action.setCheckable(True);action.setChecked(index==self.owner.preview_scale.currentIndex())
            action.triggered.connect(lambda _checked=False,i=index:self.owner.preview_scale.setCurrentIndex(i))
        menu.exec(self.quality.mapToGlobal(self.quality.rect().bottomLeft()))

    def _settings_menu(self):
        menu=QMenu(self.settings_button)
        menu.setFont(control_font())
        menu.setStyleSheet(MENU_STYLE)
        compare=menu.addAction('显示原图');compare.setCheckable(True);compare.setChecked(self.owner.compare_btn.isChecked())
        compare.setEnabled(self.owner.compare_btn.isEnabled());compare.toggled.connect(self.owner.compare_btn.setChecked)
        menu.addAction('撤销',self.owner.undo).setEnabled(self.owner.history.can_undo)
        menu.addAction('恢复',self.owner.redo).setEnabled(self.owner.history.can_redo)
        menu.addAction('图像直方图',self._show_histogram)
        menu.addSeparator();menu.addAction('返回欢迎页',self.owner.show_welcome)
        menu.exec(self.settings_button.mapToGlobal(self.settings_button.rect().bottomLeft()))

    def _show_histogram(self):
        from PySide6.QtWidgets import QVBoxLayout
        dialog=DesignDialog(self.owner,'图像直方图');dialog.resize(640,440)
        layout=QVBoxLayout(dialog.body);layout.setContentsMargins(0,0,0,0)
        plot=HistogramPanel(dialog.body,model=self.owner.histogram.model,
                            collapsible=False,motion=self.motion)
        layout.addWidget(plot)
        try:dialog.exec()
        finally:dialog.deleteLater()

    def sync(self):
        if self._syncing:return
        self._syncing=True
        try:
            sync_bindings(self)
            # Detached cards are owned by their dialogs, so a page-only walk
            # misses restores that deliberately block the canonical signals.
            for controller in self.floating:
                if controller.floating is not None:
                    sync_bindings(controller.widget)
            self.metadata.sync();self.ai_hint.setText(self.owner.ai_hint.text())
            history_key=(tuple(self.owner.history.labels()),self.owner.history.index)
            if history_key!=self._history_key:
                self.history_list.blockSignals(True);self.history_list.clear()
                if len(self.owner.history)>1:
                    for i,label in enumerate(self.owner.history.labels()):
                        self.history_list.addItem(label);self.history_list.item(i).setData(Qt.ItemDataRole.UserRole,i)
                    self.history_list.setCurrentRow(self.owner.history.index)
                self.history_list.blockSignals(False)
                self._history_key=history_key
            self.theme_button.setText('黑暗模式')
            self.quality.setText(('快速','标准','原图')[self.owner.preview_scale.currentIndex()])
            effect_tag=self.previews['effect'].tag
            layer='tag_original' if self.owner.compare_btn.isChecked() else 'tag_effect'
            effect_tag.setPixmap(self.images[layer])
            self.update();self.sync_motion()
        finally:self._syncing=False

    def sync_motion(self):
        self.card_scroll.refresh()
        for controller in self.floating:
            if controller.floating is not None and not controller.preview:
                controller.scroll._card_scroll.refresh()
        if not self.motion['enabled']:
            self._timer.stop()
            for button in self.actions.values():button.hover=button.target;button.update()
            for group in (self.lens,self.focus):
                group.animation.stop();group._finish_expansion()
        for preview in self.previews.values():
            canvas=preview.canvas
            if not hasattr(canvas,'_flock_timer'):continue
            if canvas._flock_timer.interval()!=self.motion['interval']:
                canvas._flock_timer.setInterval(self.motion['interval'])
            canvas._flock=canvas._flock[:self.motion['particles']]
            while len(canvas._flock)<self.motion['particles']:
                canvas._flock.append([random.random(),random.random(),random.uniform(-1,1),random.uniform(-1,1),random.uniform(.65,1.35),random.randint(140,255)])
            running=canvas.isVisible() and canvas._pixmap is None and self.motion['enabled'] and self.motion['flock']
            if running and not canvas._flock_timer.isActive():
                canvas._flock_last_tick=time.monotonic();canvas._flock_timer.start()
            elif not running:canvas._flock_timer.stop()

    def set_status(self,text):
        self.status_message=text
        self.update(QRectF(0,self.height()-18,self.width(),18).toAlignedRect())

    def animate(self):
        if not self.motion['enabled']:
            for button in self.actions.values():button.hover=button.target;button.update()
            return
        self._last_tick=time.monotonic();self._timer.start(self.motion['interval'])

    def _tick(self):
        now=time.monotonic();dt=min(.08,now-self._last_tick);self._last_tick=now;moving=False
        for button in self.actions.values():
            button.hover+=(button.target-button.hover)*min(1,dt*18*self.motion['speed'])
            if abs(button.hover-button.target)<.005:button.hover=button.target
            else:moving=True
            button.update()
        if not moving:self._timer.stop()

    def active_slot(self,widget):
        for controller in self.floating:
            if controller.widget is widget and controller.placeholder is not None:return controller.placeholder
        return widget

    def relayout(self):
        if not hasattr(self,'floating'):return
        w,h=self.width(),self.height()
        panels={key:rect(data['rect']) for key,data in self.data['panels'].items()}
        dx=w-4096*SCALE
        history=QRectF(panels['history']);history.translate(dx,0)
        parameters=QRectF(panels['parameters']);parameters.translate(dx,0)
        limit=self.card_height_limit()
        for name,target in (('history',history),('parameters',parameters)):
            target.setHeight(max(self.CARD_MIN_HEIGHTS[name],min(limit,limit*self.card_height_ratios[name])))
        preview=QRectF(panels['preview']);preview.setRight(history.left()-(panels['history'].left()-panels['preview'].right()))
        preview.setBottom(h-(2160*SCALE-panels['preview'].bottom()))
        changed=getattr(self,'panel_rects',None)!={'preview':preview,'history':history,'parameters':parameters}
        self.panel_rects={'preview':preview,'history':history,'parameters':parameters}
        for name,widget in [('history',self.history_panel),('parameters',self.parameters_panel)]:
            self.active_slot(widget).setGeometry(self.panel_rects[name].toAlignedRect())
        self._layout_history()
        self.scroll.setGeometry(17,48,max(100,self.parameters_panel.width()-25),max(100,self.parameters_panel.height()-60))
        self.scroll.viewport().setAutoFillBackground(False)
        self.preview_layout.relayout(preview)
        for name,grip in self.card_grips.items():
            widget=self.history_panel if name=='history' else self.parameters_panel
            target=self.panel_rects[name]
            grip.setGeometry(round(target.center().x()-24),round(target.bottom()-9),48,9)
            grip.setVisible(self.active_slot(widget) is widget);grip.raise_()
        for name,button in self.actions.items():
            target=rect(self.data['layersByName'][name]['rect']);target.translate(dx,h-2160*SCALE)
            button.setGeometry(target.toAlignedRect());button.raise_()
        if changed:self._cache=None
        self.update()

    def resizeEvent(self,event):
        super().resizeEvent(event);self.relayout()

    def showEvent(self,event):
        super().showEvent(event);self.relayout();self.sync_motion()

    def hideEvent(self,event):
        self._timer.stop()
        for preview in self.previews.values():
            if hasattr(preview.canvas,'_flock_timer'):preview.canvas._flock_timer.stop()
        super().hideEvent(event)

    def paintEvent(self,event):
        ratio=self.devicePixelRatioF()
        if self._cache is None or self._cache.devicePixelRatio()!=ratio:
            self._cache=QPixmap(round(self.width()*ratio),round(self.height()*ratio));self._cache.setDevicePixelRatio(ratio)
            self._cache.fill(Qt.GlobalColor.white)
            static=QPainter(self._cache);self._paint_static(static);static.end()
        p=QPainter(self);p.drawPixmap(0,0,self._cache)
        if self.status_message:
            p.setFont(control_font(10));p.setPen(QColor('white'))
            footer=QRectF(8,self.height()-50.111*SCALE,max(0,self.width()-220),50.111*SCALE)
            text=p.fontMetrics().elidedText(self.status_message,Qt.TextElideMode.ElideRight,round(footer.width()))
            p.drawText(footer,Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,text)

    def _paint_static(self,p):
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform);p.fillRect(self.rect(),Qt.GlobalColor.white)
        self.background.paint(p,QRectF(self.rect()))
        if not hasattr(self,'panel_rects'):return
        for name,data in self.data['panels'].items():
            layer=self.data['layersByName'][data['layer']];original=rect(data['rect']);dest=self.panel_rects[name]
            crop=rect(layer['rect'])
            outer=QRectF(dest.x()+crop.x()-original.x(),dest.y()+crop.y()-original.y(),dest.width()+crop.width()-original.width(),dest.height()+crop.height()-original.height())
            self._nine_slice(p,self.images[layer['name']],outer)
        original=rect(self.data['panels']['parameters']['rect']);offset=self.panel_rects['parameters'].topLeft()-original.topLeft()
        for name in ('parameters_pattern',):
            layer=self.data['layersByName'][name];target=rect(layer['rect']);target.translate(offset)
            p.drawPixmap(target,self.images[name],QRectF(self.images[name].rect()))
        footer=QRectF(0,self.height()-50.111*SCALE,self.width(),50.111*SCALE)
        p.fillRect(footer,QColor('#121212'));p.setFont(control_font(10));p.setPen(QColor('white'))
        p.drawText(footer.adjusted(0,0,-5,0),Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter,self.owner.version_label.text())

    @staticmethod
    def _nine_slice(painter,pixmap,target):
        xs=[0,100,pixmap.width()-240,pixmap.width()]
        ys=[0,100,pixmap.height()-100,pixmap.height()]
        dx=[target.left(),target.left()+100*SCALE,target.right()-240*SCALE,target.right()]
        dy=[target.top(),target.top()+100*SCALE,target.bottom()-100*SCALE,target.bottom()]
        for yi in range(3):
            for xi in range(3):
                source=QRectF(xs[xi],ys[yi],xs[xi+1]-xs[xi],ys[yi+1]-ys[yi])
                dest=QRectF(dx[xi],dy[yi],dx[xi+1]-dx[xi],dy[yi+1]-dy[yi])
                painter.drawPixmap(dest,pixmap,source)
