"""Nonlinear wheel scrolling for card containers, without nested inertia.

Only a scroll area's viewport and vertical scrollbar are observed. Editors and
image canvases retain their own wheel handling; ignored events can bubble to the
card container. Pixel-delta touchpad events keep their native motion directly.
"""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QApplication, QScrollArea
from shiboken6 import isValid


class CardScrollController(QObject):
    """Accumulate wheel destinations and approach them with ease-out cubic."""

    def __init__(self, area: QScrollArea, motion: dict):
        super().__init__(area)
        self.area=area;self.motion=motion
        self._bar=None;self._viewport=None;self._binding=False;self._disposed=False
        self._setting=False;self._target=0.
        self._from=self._target;self._progress=1.;self._duration=.18
        self._last_tick=time.monotonic()
        self._timer=QTimer(self);self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self._rebind_timer=QTimer(self);self._rebind_timer.setSingleShot(True)
        self._rebind_timer.timeout.connect(self.refresh)
        area.installEventFilter(self);area.destroyed.connect(self._area_destroyed)
        self._ensure_binding()
        # An explicit reveal may already be in view and therefore emit no
        # valueChanged signal. Cancel its old wheel destination before Qt runs.
        for name in ('ensureWidgetVisible','ensureVisible'):
            original=getattr(area,name)
            def reveal(*args,_original=original,**kwargs):
                self.cancel();return _original(*args,**kwargs)
            setattr(area,name,reveal)
        self.refresh()

    @property
    def is_running(self):
        return isValid(self._timer) and self._timer.isActive()

    @property
    def bar(self):
        """Return the area's current live scrollbar, rebinding after Qt replaces it."""
        return self._ensure_binding()

    @property
    def target_value(self):
        return self._target

    def _stop_timer(self):
        if isValid(self._timer):self._timer.stop()

    def _area_destroyed(self,*_):
        self._disposed=True;self._stop_timer()
        self._bar=None;self._viewport=None

    def _queue_rebind(self):
        if not self._disposed and isValid(self._rebind_timer):self._rebind_timer.start(0)

    def _bar_destroyed(self,*_):
        # Do not query QAbstractScrollArea while it is deleting its old bar;
        # setVerticalScrollBar may not have assigned the replacement yet.
        self._bar=None;self._stop_timer();self._queue_rebind()

    def _viewport_destroyed(self,*_):
        self._viewport=None;self._stop_timer();self._queue_rebind()

    def _disconnect_bar(self,bar):
        if bar is None or not isValid(bar):return
        bar.removeEventFilter(self)
        for name,slot in self._bar_slots():
            # Qt may already have torn down a connection during destruction.
            try:getattr(bar,name).disconnect(slot)
            except (RuntimeError,TypeError):pass

    def _bar_slots(self):
        return (('valueChanged',self._external_value_changed),('rangeChanged',self._range_changed),
                ('sliderPressed',self.cancel),('actionTriggered',self._action_triggered),
                ('destroyed',self._bar_destroyed))

    def _ensure_binding(self):
        """Observe live Qt children rather than retaining a deleted C++ wrapper."""
        if self._disposed or not isValid(self) or not isValid(self.area):return None
        if self._binding:return self._bar if self._bar is not None and isValid(self._bar) else None
        self._binding=True
        try:
            # A QWidget can still be valid briefly while its children are being
            # destroyed. Invalid children are left alone until the next turn.
            try:bar=self.area.verticalScrollBar();viewport=self.area.viewport()
            except RuntimeError:return None
            if bar is None or not isValid(bar):return None
            if bar is not self._bar:
                self._disconnect_bar(self._bar);self._bar=bar
                bar.installEventFilter(self)
                for name,slot in self._bar_slots():getattr(bar,name).connect(slot)
                self._stop_timer();self._target=float(bar.value())
                self._from=self._target;self._progress=1.
            if viewport is not self._viewport and viewport is not None and isValid(viewport):
                if self._viewport is not None and isValid(self._viewport):
                    self._viewport.removeEventFilter(self)
                    self._viewport.destroyed.disconnect(self._viewport_destroyed)
                self._viewport=viewport;viewport.installEventFilter(self)
                viewport.destroyed.connect(self._viewport_destroyed)
            return bar
        finally:self._binding=False

    def _clamp(self,value):
        bar=self._ensure_binding()
        return max(float(bar.minimum()),min(float(bar.maximum()),value)) if bar is not None else 0.

    def _speed(self):
        try:value=float(self.motion.get('speed',1.))
        except (TypeError,ValueError):return 1.
        return max(.1,min(8.,value)) if math.isfinite(value) else 1.

    def refresh(self):
        """Apply current animation and performance settings without restarting."""
        if self._ensure_binding() is None or not isValid(self._timer):
            self._stop_timer();return
        try:interval=int(self.motion.get('interval',8))
        except (TypeError,ValueError):interval=8
        interval=max(4,min(1000,interval))
        if self._timer.interval()!=interval:self._timer.setInterval(interval)
        if not self.motion.get('enabled',True) and self.is_running:self._finish()

    def cancel(self):
        """Let a scrollbar drag, key or explicit navigation take over now."""
        self._stop_timer();bar=self._ensure_binding()
        self._target=float(bar.value()) if bar is not None else 0.
        self._from=self._target;self._progress=1.

    def _set_value(self,value):
        bar=self._ensure_binding()
        if bar is None:self._stop_timer();return
        self._setting=True
        try:bar.setValue(round(self._clamp(value)))
        finally:self._setting=False

    def _external_value_changed(self,*_):
        if not self._setting:self.cancel()

    def _range_changed(self,*_):
        self.cancel()

    def _action_triggered(self,*_):
        self.cancel()

    def _finish(self):
        self._set_value(self._target);self._stop_timer();bar=self._ensure_binding()
        self._target=float(bar.value()) if bar is not None else 0.;self._progress=1.

    def _advance(self,now):
        if not self.is_running:return
        dt=max(0.,now-self._last_tick);self._last_tick=now
        self._progress=min(1.,self._progress+dt*self._speed()/self._duration)
        eased=1.-(1.-self._progress)**3
        self._set_value(self._from+(self._target-self._from)*eased)
        if self._progress>=1.:self._finish()

    def _tick(self):
        self.refresh()
        self._advance(time.monotonic())

    def _scroll(self,delta,*,direct=False):
        self.refresh()
        self._advance(time.monotonic())
        bar=self._ensure_binding()
        if bar is None:return False
        current=float(bar.value())
        target=self._clamp((current if direct or not self.is_running else self._target)+delta)
        if target==current:
            self.cancel();return False
        self._target=target
        if direct or not self.motion.get('enabled',True):
            self._finish();return True
        self._from=current;self._progress=0.
        self._duration=max(.16,min(.30,.16+abs(target-current)*.0002))
        self._last_tick=time.monotonic();self._timer.start()
        return True

    def eventFilter(self,watched,event):
        if self._disposed or not isValid(self.area):return False
        kind=event.type()
        if kind in (QEvent.Type.Show,QEvent.Type.ParentChange,QEvent.Type.StyleChange):
            self._ensure_binding()
        if kind in (QEvent.Type.Hide,QEvent.Type.MouseButtonPress,QEvent.Type.KeyPress):
            self.cancel()
        elif kind==QEvent.Type.Resize and watched is self._viewport:
            self.cancel()
        if kind!=QEvent.Type.Wheel:return False
        bar=self._ensure_binding()
        if bar is None or watched not in (self._viewport,bar):return False
        if event.modifiers()&(Qt.KeyboardModifier.ControlModifier|Qt.KeyboardModifier.ShiftModifier):return False
        if bar.isSliderDown():self.cancel();return False
        pixels=event.pixelDelta();angle=event.angleDelta()
        if not pixels.isNull():
            if not pixels.y() or abs(pixels.x())>abs(pixels.y()):return False
            self.cancel();consumed=self._scroll(-pixels.y(),direct=True)
        else:
            if not angle.y() or abs(angle.x())>abs(angle.y()):return False
            lines=QApplication.wheelScrollLines()
            if lines==0:return False
            distance=bar.pageStep() if lines<0 else max(1,bar.singleStep())*lines
            consumed=self._scroll(-angle.y()/120.*distance)
        if consumed:event.accept()
        return consumed


def install_card_scroll(area: QScrollArea, motion: dict):
    """Install once on an existing area; return its reusable controller."""
    controller=getattr(area,'_card_scroll',None)
    if controller is None:
        controller=CardScrollController(area,motion);area._card_scroll=controller
    else:
        controller.motion=motion;controller.refresh()
    return controller
