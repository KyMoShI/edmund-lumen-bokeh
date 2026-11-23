"""Offline checks for card wheel interpolation and direct-input takeover."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import numpy as np
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT/'.venv/Lib/site-packages'));sys.path.insert(0,str(ROOT/'app'))
from PySide6.QtCore import QCoreApplication,QEvent,QPoint,QPointF,Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QComboBox,QLabel,QScrollArea,QScrollBar,QSpinBox,QVBoxLayout,QWidget
from shiboken6 import isValid

from card_motion import install_card_scroll


def wheel(widget,delta=-120,*,pixels=0):
    position=QPointF(widget.rect().center())
    event=QWheelEvent(position,QPointF(widget.mapToGlobal(position.toPoint())),QPoint(0,pixels),QPoint(0,delta),
                      Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier,Qt.ScrollPhase.NoScrollPhase,False)
    QApplication.sendEvent(widget,event)


def run():
    app=QApplication.instance() or QApplication([])
    motion={'enabled':True,'speed':1.,'interval':8}
    area=QScrollArea();area.resize(360,260);area.setWidgetResizable(True)
    content=QWidget();content.setMinimumHeight(1600)
    layout=QVBoxLayout(content);top=QLabel('参数卡片');layout.addWidget(top)
    combo=QComboBox();combo.addItems(['一','二','三']);layout.addWidget(combo)
    spin=QSpinBox();spin.setRange(0,100);spin.setValue(50);layout.addWidget(spin)
    layout.addStretch();area.setWidget(content)
    controller=install_card_scroll(area,motion);area.show();app.processEvents()
    bar=area.verticalScrollBar();bar.setSingleStep(20)
    try:
        assert install_card_scroll(area,motion) is controller
        wheel(area.viewport());target=controller.target_value
        assert controller.is_running and bar.value()==0 and target>0
        duration=controller._duration
        controller._advance(controller._last_tick+duration/2)
        middle=bar.value()
        assert abs(middle/target-.875)<.025,(middle,target)
        controller._advance(controller._last_tick+duration*.51)
        assert bar.value()==round(target) and not controller.is_running
        print('PASS: ease-out cubic midpoint, exact destination, idle timer and idempotent install')

        bar.setValue(0);wheel(area.viewport());first=controller.target_value
        wheel(area.viewport());assert controller.target_value==first*2
        controller._finish();assert bar.value()==round(first*2)
        bar.setValue(bar.maximum()-5);wheel(area.viewport());controller._finish()
        assert bar.value()==bar.maximum()
        wheel(area.viewport());assert not controller.is_running and bar.value()==bar.maximum()
        bar.setValue(3);wheel(area.viewport(),120);controller._finish();assert bar.value()==0
        print('PASS: accumulated wheel targets and both bounded edges')

        bar.setValue(0);wheel(area.viewport());controller._advance(controller._last_tick+.04)
        slower=bar.value();controller.cancel();bar.setValue(0);motion['speed']=2.
        wheel(area.viewport());controller._advance(controller._last_tick+.04)
        assert bar.value()>slower
        motion['interval']=33;controller.refresh();assert controller._timer.interval()==33
        destination=controller.target_value;motion['enabled']=False;controller.refresh()
        assert bar.value()==round(destination) and not controller.is_running
        before=bar.value();wheel(area.viewport());assert bar.value()>before and not controller.is_running
        motion.update(enabled=True,speed=1.,interval=8)
        print('PASS: speed, performance interval and immediate disabled-animation behavior')

        bar.setValue(100);wheel(area.viewport());assert controller.is_running
        wheel(area.viewport(),0,pixels=-17)
        assert bar.value()==117 and controller.target_value==117 and not controller.is_running
        wheel(area.viewport(),0,pixels=9);assert bar.value()==108
        print('PASS: pixel-delta touchpad movement is direct without additional inertia')

        bar.setValue(0);wheel(area.viewport());bar.setValue(12)
        assert not controller.is_running and controller.target_value==12
        wheel(area.viewport());bar.setSliderDown(True)
        assert not controller.is_running
        bar.setSliderDown(False);bar.setValue(0);wheel(area.viewport())
        area.ensureWidgetVisible(top,0,0)
        assert not controller.is_running,'An already visible explicit reveal retained an old wheel target'
        wheel(area.viewport());area.ensureVisible(0,0)
        assert not controller.is_running
        wheel(area.viewport());area.hide();app.processEvents();assert not controller.is_running
        area.show();app.processEvents();bar.setValue(0)
        print('PASS: external scroll, scrollbar drag, explicit reveal and hidden-page takeover')

        before=spin.value();wheel(spin,120)
        assert spin.value()>before and not controller.is_running
        before=combo.currentIndex();wheel(combo,-120)
        assert combo.currentIndex()>before and not controller.is_running
        print('PASS: numeric editors and dropdowns retain native wheel behavior')

        old_bar=bar;replacement=QScrollBar(Qt.Orientation.Vertical)
        area.setVerticalScrollBar(replacement);app.processEvents()
        assert not isValid(old_bar),'Qt did not delete the replaced scrollbar'
        assert controller.bar is replacement and isValid(controller.bar)
        bar=replacement;bar.setValue(0);wheel(area.viewport())
        assert controller.is_running and controller.target_value>0
        controller._finish();assert bar.value()==round(controller.target_value)
        area.ensureWidgetVisible(top);assert not controller.is_running
        print('PASS: deleted scrollbar is replaced by a live binding with working wheel and explicit reveal')
    finally:
        area.close();app.processEvents()
    area.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    assert not isValid(area)
    controller.cancel();controller.refresh();assert not controller.is_running
    print('PASS: cancellation during completed Qt owner/timer destruction is safe')

    import main
    old_path=main._SETTINGS_PATH;old_motion=main.MOTION_DEFAULTS.copy()
    with tempfile.TemporaryDirectory(prefix='card-motion-') as folder:
        main._SETTINGS_PATH=Path(folder)/'settings.ini';main.MOTION_DEFAULTS['enabled']=False
        window=None
        try:
            window=main.MainWindow();window._device_profile=lambda:{}
            window.resize(1366,720);window.show();app.processEvents()
            window.welcome_page.buttons['start'].click();app.processEvents()
            page=window.light_workspace
            assert page.scroll._card_scroll.motion is main.MOTION
            if hasattr(window,'_dark_card_scroll'):
                assert window._dark_card_scroll.motion is main.MOTION
            page.scroll.verticalScrollBar().setValue(0)
            main.MOTION.update(enabled=True,speed=1.,interval=8);window._apply_motion_settings()
            wheel(page.scroll.viewport());assert page.card_scroll.is_running
            page.scroll.ensureWidgetVisible(page.metadata)
            assert not page.card_scroll.is_running
            floating=next(item for item in page.floating if item.widget is page.lens)
            floating.detach();app.processEvents()
            assert floating.scroll._card_scroll.motion is main.MOTION
            floating.restore();app.processEvents()
            print('PASS: live parameter cards, detached groups and dark shared settings install')
            complete=next(item for item in page.floating if item.widget is page.parameters_panel)
            for cycle in range(3):
                complete.detach();app.processEvents();QTest.qWait(10)
                page.scroll.ensureWidgetVisible(page.rows['bokeh_size'].value)
                page.scroll.verticalScrollBar().setValue(0)
                wheel(page.scroll.viewport());assert page.card_scroll.is_running
                complete.restore();app.processEvents()
                QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
                page.scroll.ensureWidgetVisible(page.rows['bokeh_size'].value)
                assert isValid(page.card_scroll.bar)
                assert page.card_scroll.bar is page.scroll.verticalScrollBar()
                page.scroll.verticalScrollBar().setValue(0)
                wheel(page.scroll.viewport());assert page.card_scroll.is_running
                page.scroll.ensureWidgetVisible(page.metadata)
                assert not page.card_scroll.is_running
            print('PASS: repeated complete parameter-card detach/restore preserves live scrollbar, reveal and wheel')
        finally:
            if window is not None:window.close()
            app.processEvents();main._SETTINGS_PATH=old_path
            main.MOTION_DEFAULTS.clear();main.MOTION_DEFAULTS.update(old_motion)
    print('PASS: nonlinear card scrolling; no network or real user settings used.')


if __name__=='__main__':run()
