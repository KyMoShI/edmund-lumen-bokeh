"""PDF-derived welcome scene with native, keyboard-accessible actions."""
from __future__ import annotations

import json
import math
from pathlib import Path
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QAbstractButton, QWidget

from welcome_scene import WelcomeScene
from workspace_controls import control_font


PROMPT_TEXT='今天我们要模拟什么'


class WelcomeAction(QAbstractButton):
    def __init__(self, page, name, caption):
        super().__init__(page)
        self.page, self.name = page, name
        self.level = 0.0
        self.target = 0.0
        self.setText(caption)
        self.setAccessibleName(caption)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip('进入主工作区' if name == 'start' else '继续上次工作')
        self.clicked.connect(lambda: page.choose(name))

    def paintEvent(self, event):
        # The parent paints the source artwork in the action's anchored group.
        pass

    def hitButton(self, pos):
        data = self.page.layout_data['buttons'][self.name]
        rect = self.page.button_rect(self.name).translated(-self.x(), -self.y())
        scale, _ = self.page.viewport()
        radius = data['radius']*scale
        shape = QPainterPath()
        shape.addRoundedRect(rect, radius, radius)
        return shape.contains(QPointF(pos))

    def enterEvent(self, event):
        self.target = 1.0
        self.page.animate()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.target = 0.0
        self.page.animate()
        super().leaveEvent(event)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.page.update()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.page.update()

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        self.page.update()

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.page.update()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if not event.isAutoRepeat():
                self.click()
            event.accept()
        else:
            super().keyPressEvent(event)
        self.page.update()

    def keyReleaseEvent(self, event):
        super().keyReleaseEvent(event)
        self.page.update()


class WelcomePage(QWidget):
    actionRequested = Signal(str)

    def __init__(self, assets: Path, motion: dict, parent=None):
        super().__init__(parent)
        self.setObjectName('WelcomePage')
        self.setAccessibleName('欢迎页')
        self.setAccessibleDescription('伯爵大光圈。START 进入工作区，NEXT 继续上次工作。')
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.motion = motion
        self.layout_data = json.loads((assets / 'layout.json').read_text(encoding='utf-8'))
        self.layers = {item['name']: item for item in self.layout_data['layers']}
        self.pixmaps = {name: QPixmap(str(assets / item['file'])) for name, item in self.layers.items()
                        if name != 'background'}
        if any(pixmap.isNull() for pixmap in self.pixmaps.values()):
            raise RuntimeError('欢迎页图层资源不完整。')
        self.scene=WelcomeScene(assets)
        self.buttons = {name: WelcomeAction(self, name, caption)
                        for name, caption in (('start', 'START'), ('next', 'NEXT'))}
        QWidget.setTabOrder(self.buttons['start'], self.buttons['next'])
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self._hold_timer=QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._hold_timer.timeout.connect(self._begin_prompt)
        self._cursor_timer=QTimer(self)
        self._cursor_timer.setInterval(500)
        self._cursor_timer.timeout.connect(self._blink_cursor)
        self._started = self._last_tick = time.monotonic()
        self._intro = 0.0
        self._typed = 0
        self._title_stage='brand'
        self._prompt_typed=0
        self._prompt_started=None
        self._hold_started=self._hold_deadline=None
        self._cursor_visible=False
        self._prompt_font_cache=None
        self._exit_action = None
        self._exit_started = None
        self._scene_opacity = 1.0
        self._cache = None
        self._cache_key = None

    def viewport(self):
        width, height = self.layout_data['artboard']
        scale = min(self.width() / width, self.height() / height)
        # This is the brand group's transform. Background, scenery and the
        # two native actions use independent layer/edge anchors.
        return scale, QPointF(0,self.height()-height*scale)

    def map_rect(self, rect, anchor='bottom-left'):
        scale, origin = self.viewport()
        source_width,source_height=self.layout_data['artboard']
        if anchor.endswith('right'):
            origin.setX(self.width()-source_width*scale)
        if anchor.startswith('top'):
            origin.setY(0)
        x, y, w, h = rect
        return QRectF(origin.x()+x*scale, origin.y()+y*scale, w*scale, h*scale)

    def button_rect(self,name):
        anchor='top-right' if name=='next' else 'bottom-right'
        return self.map_rect(self.layout_data['buttons'][name]['rect'],anchor)

    def resizeEvent(self, event):
        self._cache = None
        for name, button in self.buttons.items():
            button.setGeometry(self.button_rect(name).toAlignedRect())
        super().resizeEvent(event)

    def present(self):
        self._timer.stop();self._hold_timer.stop();self._cursor_timer.stop()
        self._started = self._last_tick = time.monotonic()
        animated_title=self._title_enabled()
        self._intro = 0.0 if animated_title else 1.0
        self._typed = 0 if animated_title else 5
        self._title_stage='brand' if animated_title else 'done'
        self._prompt_typed=0 if animated_title else len(PROMPT_TEXT)
        self._prompt_started=self._hold_started=self._hold_deadline=None
        self._cursor_visible=True
        self._exit_action = self._exit_started = None
        self._scene_opacity = 1.0
        for button in self.buttons.values():
            button.setEnabled(True)
            button.level = button.target = 0.0
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        if animated_title:self._cursor_timer.start()
        self.animate()

    def _title_enabled(self):
        return bool(self.motion.get('enabled',True) and self.motion.get('title',True))

    @property
    def displayed_title(self):
        if self._title_stage in ('brand','hold'):
            return self.layout_data['brandText']['text'][:self._typed]
        return PROMPT_TEXT[:self._prompt_typed]

    def _settle_title(self):
        self._hold_timer.stop();self._cursor_timer.stop()
        self._typed=5;self._title_stage='done';self._prompt_typed=len(PROMPT_TEXT)
        self._cursor_visible=True

    def _advance_title(self,now):
        if not self._title_enabled():
            self._settle_title();return
        speed=max(.1,float(self.motion.get('speed',1)))
        if self._title_stage=='brand':
            self._typed=min(5,max(0,int(((now-self._started)*speed-.20)/.12)))
            if self._typed==5:
                self._title_stage='hold'
                self._hold_started=now;self._hold_deadline=now+3.0
                self._hold_timer.start(3000)
        elif self._title_stage=='prompt':
            self._prompt_typed=min(len(PROMPT_TEXT),int((now-self._prompt_started)*speed/.12))
            if self._prompt_typed==len(PROMPT_TEXT):self._title_stage='done'

    def _begin_prompt(self):
        if not self.isVisible() or self._exit_action is not None:return
        if not self._title_enabled():
            self._settle_title();self.update();return
        if self._title_stage!='hold':return
        now=time.monotonic()
        remaining=self._hold_deadline-now
        if remaining>0:
            self._hold_timer.start(max(1,math.ceil(remaining*1000)));return
        self._title_stage='prompt';self._prompt_started=now;self._prompt_typed=0
        self._cursor_visible=True
        self._cursor_timer.start()
        self.animate();self.update()

    def _blink_cursor(self):
        if not self.isVisible() or self._exit_action is not None:
            self._cursor_timer.stop();self._cursor_visible=False;return
        if not self._title_enabled():
            self._settle_title();self.update();return
        self._cursor_visible=not self._cursor_visible
        text_rect=self.map_rect(self.layers[self.layout_data['brandText']['layer']]['rect'])
        self.update(text_rect.adjusted(-3,-3,text_rect.height()*.2,3).toAlignedRect())

    def animate(self):
        if not self.motion['enabled']:
            for button in self.buttons.values():
                button.level = button.target
            self._intro, self._typed = 1.0, 5
            self._settle_title()
            self.update()
            return
        if not self._title_enabled():self._settle_title()
        self._last_tick = time.monotonic()
        active=(self._intro<1 or self._title_stage in ('brand','prompt')
                or self._exit_started is not None
                or any(abs(button.level-button.target)>=.005 for button in self.buttons.values()))
        if active and not self._timer.isActive():
            self._timer.start(int(self.motion['interval']))
        self.update()

    def choose(self, action):
        if self._exit_action is not None:
            return
        self._exit_action = action
        self._hold_timer.stop();self._cursor_timer.stop();self._cursor_visible=False
        for button in self.buttons.values():
            button.setEnabled(False)
        if not self.motion['enabled']:
            self.actionRequested.emit(action)
            return
        self._exit_started = time.monotonic()
        self._timer.start(int(self.motion['interval']))

    def _tick(self):
        now = time.monotonic()
        speed = float(self.motion['speed'])
        elapsed = (now-self._started)*speed
        self._intro = min(1.0, elapsed/.55) if self._title_enabled() else 1.0
        if self._exit_action is None:self._advance_title(now)
        moving = False
        dt = min(.08, max(0, now-self._last_tick))
        self._last_tick = now
        for button in self.buttons.values():
            button.level += (button.target-button.level)*(1-math.exp(-dt*20*speed))
            if abs(button.level-button.target) < .005:
                button.level = button.target
            else:
                moving = True
        if self._exit_started is not None:
            progress = min(1.0, (now-self._exit_started)*speed/.18)
            self._scene_opacity = 1.0-progress
            if progress >= 1:
                self._timer.stop()
                self.actionRequested.emit(self._exit_action)
                return
        elif self._intro == 1 and self._title_stage in ('hold','done') and not moving:
            self._timer.stop()
        self.update()

    def hideEvent(self, event):
        self._timer.stop();self._hold_timer.stop();self._cursor_timer.stop()
        super().hideEvent(event)

    def _prompt_layout(self,bounds):
        key=(bounds.width(),bounds.height())
        if self._prompt_font_cache is not None and self._prompt_font_cache[0]==key:
            return self._prompt_font_cache[1:]
        scale,_=self.viewport()
        low,high=1,max(1,round(self.layout_data['brandText']['font']['sourceSize']*scale))
        gap=max(2,bounds.height()*.025)
        while low<high:
            size=(low+high+1)//2;candidate=control_font(size,bold=True);metrics=QFontMetricsF(candidate)
            if metrics.horizontalAdvance(PROMPT_TEXT)+gap+2<=bounds.width() and metrics.height()<=bounds.height():low=size
            else:high=size-1
        font=control_font(low,bold=True);metrics=QFontMetricsF(font)
        self._prompt_font_cache=(key,font,metrics,gap)
        return font,metrics,gap

    def _paint_title(self,painter):
        text=self.layout_data['brandText'];source_bounds=self.layers[text['layer']]['rect']
        bounds=self.map_rect(source_bounds)
        if self._title_stage in ('brand','hold'):
            painter.save()
            if self._typed<5:
                end=source_bounds[0] if self._typed==0 else text['glyphRightEdges'][self._typed-1]
                painter.setClipRect(self.map_rect([source_bounds[0],source_bounds[1],max(0,end-source_bounds[0]),source_bounds[3]]))
            self._paint_layer(painter,text['layer']);painter.restore()
            edge=source_bounds[0] if self._typed==0 else text['glyphRightEdges'][self._typed-1]
            cursor_x=self.map_rect([edge,source_bounds[1],0,source_bounds[3]]).left()+max(1,bounds.height()*.025)
            cursor_top=bounds.top()+bounds.height()*.08;cursor_bottom=bounds.bottom()-bounds.height()*.08
        else:
            font,metrics,gap=self._prompt_layout(bounds)
            value=PROMPT_TEXT[:self._prompt_typed]
            baseline=bounds.center().y()+(metrics.ascent()-metrics.descent())/2
            painter.save();painter.setClipRect(bounds);painter.setFont(font);painter.setPen(QColor('white'))
            painter.drawText(QPointF(bounds.left(),baseline),value);painter.restore()
            cursor_x=bounds.left()+metrics.horizontalAdvance(value)+gap
            cursor_top=baseline-metrics.ascent()*.85;cursor_bottom=baseline+metrics.descent()*.45
        if self._cursor_visible:
            painter.setPen(QPen(QColor('white'),max(1.2,bounds.height()*.018),Qt.PenStyle.SolidLine,Qt.PenCapStyle.FlatCap))
            painter.drawLine(QPointF(cursor_x,cursor_top),QPointF(cursor_x,cursor_bottom))

    def _paint_layer(self, painter, name):
        item = self.layers[name]
        pixmap = self.pixmaps[name]
        anchor='top-right' if name=='next' else 'bottom-right' if name=='start' else 'bottom-left'
        painter.drawPixmap(self.map_rect(item['rect'],anchor), pixmap, QRectF(pixmap.rect()))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.GlobalColor.white)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        key = (self.width(), self.height(), self.devicePixelRatioF())
        if self._cache is None or self._cache_key != key:
            ratio = self.devicePixelRatioF()
            self._cache = QPixmap(round(self.width()*ratio), round(self.height()*ratio))
            self._cache.setDevicePixelRatio(ratio)
            self._cache.fill(Qt.GlobalColor.white)
            cache_painter = QPainter(self._cache)
            cache_painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            self.scene.paint(cache_painter,QRectF(self.rect()))
            cache_painter.end()
            self._cache_key = key
        opacity = (1-(1-self._intro)**3)*self._scene_opacity
        painter.setOpacity(opacity)
        painter.drawPixmap(0, 0, self._cache)
        for name in ('brand_panel', 'mark'):
            self._paint_layer(painter, name)
        self._paint_title(painter)
        for name, button in self.buttons.items():
            painter.save()
            painter.translate(0, (1.2 if button.isDown() else -2.5*button.level))
            self._paint_layer(painter, self.layout_data['buttons'][name]['layer'])
            if button.hasFocus() or button.level > .01:
                rect = self.button_rect(name).adjusted(-4,-4,4,4)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                color = QColor('#001EFF')
                color.setAlphaF(1.0 if button.hasFocus() else button.level*.45)
                painter.setPen(QPen(color, 1.5))
                scale, _ = self.viewport()
                radius = self.layout_data['buttons'][name]['radius']*scale+4
                painter.drawRoundedRect(rect, radius, radius)
            painter.restore()
