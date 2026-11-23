"""Exercise welcome navigation, geometry, motion and restart recovery offscreen."""
from pathlib import Path
import os
import sys
import tempfile
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
try:
    import cv2
except ModuleNotFoundError:
    sys.path.append(str(ROOT/'.venv/Lib/site-packages'))
    import cv2
sys.path.insert(0,str(ROOT/'app'))
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from main import MainWindow
import main
from welcome import WelcomePage


def run():
    app=QApplication.instance() or QApplication([])
    app.setApplicationName('Welcome checks')
    output=ROOT/'tmp/welcome-checks'; output.mkdir(parents=True,exist_ok=True)
    windows=[]
    old_path=main._SETTINGS_PATH
    old_motion=main.MOTION_DEFAULTS.copy()
    with tempfile.TemporaryDirectory(prefix='bjdgq-welcome-') as directory:
        directory=Path(directory)
        main._SETTINGS_PATH=directory/'settings.ini'
        main.MOTION_DEFAULTS['enabled']=False
        try:
            w=MainWindow(); windows.append(w); w.resize(1366,720); w.show(); app.processEvents()
            assert w.pages.currentWidget() is w.welcome_page
            assert not w.statusBar().isVisible()
            w.log_device_profile()
            assert not w._device_profile_logged, 'Welcome collected blocking device metadata'
            assert not any(action.isEnabled() for action in w.actions())
            w.grab().save(str(output/'welcome.png'))
            # Each anchored layer shares its paint and hit transform.
            for size in ((1366,720),(1133,1001),(1000,800),(1280,800),(1920,1080),(2560,1080),(1024,768)):
                w.resize(*size); app.processEvents()
                page=w.welcome_page; scale,offset=page.viewport()
                assert abs(scale*4096/(scale*2160)-4096/2160)<1e-9
                for name,button in page.buttons.items():
                    expected=page.button_rect(name)
                    assert button.geometry().contains(expected.center().toPoint())
                    assert button.hitButton(button.rect().center())
                assert not page.buttons['start'].hitButton(QPoint(0,0))
                assert abs(page.button_rect('next').top()-page.layout_data['buttons']['next']['rect'][1]*scale)<1
                source_bottom=sum(page.layout_data['buttons']['start']['rect'][i] for i in (1,3))
                assert abs(page.button_rect('start').bottom()-(page.height()-(2160-source_bottom)*scale))<1
            w.resize(1366,720); app.processEvents()
            QTest.mouseClick(w.welcome_page.buttons['start'],Qt.MouseButton.LeftButton)
            app.processEvents()
            assert w.is_workspace_active() and w.original_full is None
            assert all(action.isEnabled() for action in w.actions())
            source=directory/'测试图片.png'
            pixels=np.zeros((96,128,3),np.uint8); pixels[:]=[40,90,160]
            Image.fromarray(pixels).save(source)
            w.open_image_path(str(source))
            depth=np.linspace(0,1,128*96,dtype=np.float32).reshape(96,128)
            w.apply_depth_map(depth,'测试深度')
            w.full_luminance_bokeh.setChecked(True)
            w.blur_slider.setValue(143)
            w.bokeh_size.setValue(411)
            w.range_check.setChecked(True)
            w._history_timer.stop(); w._commit_history()
            history=w.history; count=len(history)
            w.show_welcome(); app.processEvents()
            assert w.pages.currentWidget() is w.welcome_page
            assert not w.before_canvas._flock_timer.isActive()
            QTest.keyClick(w.welcome_page,Qt.Key.Key_Tab)
            assert w.welcome_page.buttons['start'].hasFocus()
            QTest.keyClick(w.welcome_page.buttons['start'],Qt.Key.Key_Tab)
            assert w.welcome_page.buttons['next'].hasFocus()
            QTest.keyClick(w.welcome_page.buttons['next'],Qt.Key.Key_Return)
            app.processEvents()
            assert w.history is history and len(w.history)==count
            assert w.blur_slider.value()==143 and w.bokeh_size.value()==411
            assert w.range_check.isChecked() and np.array_equal(w._depth_full,depth)
            # START opens a blank workspace; NEXT can still recover the saved work.
            w.show_welcome(); w.welcome_page.buttons['start'].click(); app.processEvents()
            assert w.original_full is None and w.depth is None
            w.close(); app.processEvents()
            cache=directory/'sessions/last-session.npz'; before=cache.read_bytes()
            w2=MainWindow(); windows.append(w2); w2.show(); app.processEvents()
            w2.close(); app.processEvents()
            assert cache.read_bytes()==before, 'Closing welcome overwrote saved work'
            w3=MainWindow(); windows.append(w3); w3.show(); app.processEvents()
            w3.welcome_page.buttons['next'].click(); app.processEvents()
            assert np.array_equal(w3.original_full,pixels)
            assert np.array_equal(w3._depth_full,depth)
            assert w3.blur_slider.value()==143 and w3.bokeh_size.value()==411
            assert w3.range_check.isChecked()
            assert len(w3.history)==1
            w3.close(); app.processEvents()
            source.unlink()
            w4=MainWindow(); windows.append(w4); w4.show(); app.processEvents()
            w4.welcome_page.buttons['next'].click(); app.processEvents()
            assert w4.original_full is None and w4.depth is None
            assert w4.blur_slider.value()==143
            assert '不可用' in w4.statusBar().currentMessage()
            w4.close(); app.processEvents()
            # Replacing an open source before saving must not validate old depth.
            Image.fromarray(pixels).save(source)
            w5=MainWindow(); windows.append(w5); w5.show(); app.processEvents()
            w5.welcome_page.buttons['start'].click(); w5.open_image_path(str(source))
            w5.apply_depth_map(depth,'旧深度')
            changed=pixels.copy(); changed[:]=[190,45,20]
            Image.fromarray(changed).save(source)
            w5.show_welcome(); w5.close(); app.processEvents()
            w6=MainWindow(); windows.append(w6); w6.show(); app.processEvents()
            w6.welcome_page.buttons['next'].click(); app.processEvents()
            assert np.array_equal(w6.original_full,changed)
            assert w6.depth is None and w6._depth_full is None
            assert '更新' in w6.statusBar().currentMessage()
            w6.close(); app.processEvents()
            # The first title holds for three real seconds; only its cursor
            # continues at low frequency while the text itself is stationary.
            motion=main.MOTION_DEFAULTS.copy(); motion['enabled']=True
            page=WelcomePage(main.asset('welcome'),motion); windows.append(page)
            page.resize(1366,720); page.show(); page.present()
            QTest.qWait(400)
            assert 0<page._typed<5
            page.grab().save(str(output/'typewriter.png'))
            QTest.qWait(600)
            assert page._typed==5 and page._intro==1 and not page._timer.isActive()
            assert page._title_stage=='hold' and page._hold_timer.isActive()
            assert page._cursor_timer.isActive() and page._cursor_timer.interval()==500
            assert page.displayed_title=='伯爵大光圈'
            page.grab().save(str(output/'settled.png'))
            deadline=page._hold_deadline
            assert abs(deadline-page._hold_started-3.0)<1e-9
            QTest.qWait(max(1,int((deadline-time.monotonic())*1000)-100))
            assert page._title_stage=='hold' and page.displayed_title=='伯爵大光圈'
            QTest.qWait(120)
            assert page._title_stage=='prompt' and page._prompt_typed==0
            assert page.displayed_title=='', 'The first title still overlaps the prompt'
            QTest.qWait(300)
            assert 0<page._prompt_typed<len('今天我们要模拟什么')
            page.grab().save(str(output/'prompt-typing.png'))
            QTest.qWait(1000)
            assert page._title_stage=='done' and page.displayed_title=='今天我们要模拟什么'
            assert not page._timer.isActive() and not page._hold_timer.isActive()
            assert page._cursor_timer.isActive()
            before_cursor=page._cursor_visible
            QTest.qWait(page._cursor_timer.remainingTime()+40)
            assert page._cursor_visible!=before_cursor
            page.grab().save(str(output/'prompt-complete.png'))
            page.resize(1000,800); app.processEvents()
            page.grab().save(str(output/'narrow.png'))
            page.hide();app.processEvents()
            assert not any(timer.isActive() for timer in (page._timer,page._hold_timer,page._cursor_timer))
            page.show();page.present();app.processEvents()
            assert page._title_stage=='brand' and page._typed==0
            # Speed changes the typing phase, not the actual hold duration.
            page.motion['speed']=2;page.present();QTest.qWait(480)
            assert page._title_stage=='hold'
            assert abs(page._hold_deadline-page._hold_started-3.0)<1e-9
            page.choose('start')
            assert not page._hold_timer.isActive() and not page._cursor_timer.isActive()
            QTest.qWait(150)
            assert not page._timer.isActive()
            page.close(); app.processEvents()
            for enabled,title in ((False,True),(True,False)):
                static=WelcomePage(main.asset('welcome'),{'enabled':enabled,'title':title,'interval':8,'speed':1})
                windows.append(static);static.resize(1366,720);static.show();static.present();app.processEvents()
                assert static.displayed_title=='今天我们要模拟什么' and static._cursor_visible
                assert not any(timer.isActive() for timer in (static._timer,static._hold_timer,static._cursor_timer))
                static.close()
            # Pin the original complete brand for source-artwork calibration;
            # normal static presentation now shows the final prompt instead.
            still=WelcomePage(main.asset('welcome'),{'enabled':False,'title':False,'interval':8,'speed':1})
            windows.append(still); still.resize(4096,2160); still.show(); still.present(); app.processEvents()
            still._title_stage='hold';still._typed=5;still._cursor_visible=False;still.update();app.processEvents()
            still.grab().save(str(output/'source-size.png'))
            rgba=Image.new('RGBA',(4096,2160),'white')
            for item in still.layout_data['layers']:
                with Image.open(main.asset('welcome',item['file'])) as layer:
                    rgba.alpha_composite(layer.convert('RGBA'),tuple(item['rect'][:2]))
            with Image.open(output/'source-size.png') as actual:
                logical=actual.convert('RGB').resize((4096,2160),Image.Resampling.LANCZOS)
                difference=np.abs(np.asarray(logical,dtype=np.int16)-np.asarray(rgba.convert('RGB'),dtype=np.int16))
            assert float(difference.mean())<1,float(difference.mean())
            still.close(); app.processEvents()
            print('PASS: startup, aspect/hit mapping, mouse/keyboard navigation, warm history, cold image/depth/extended-parameter recovery, missing/replaced source, untouched cache, two-stage typing, real three-second hold, low-frequency cursor, static fallback, hidden/navigation timer cleanup, source-sized artwork.')
            print(f'Idle source-size mean channel error: {difference.mean():.6f}/255')
        finally:
            for window in windows:
                window.close()
            app.processEvents()
            main._SETTINGS_PATH=old_path
            main.MOTION_DEFAULTS.clear(); main.MOTION_DEFAULTS.update(old_motion)


if __name__=='__main__':
    run()
