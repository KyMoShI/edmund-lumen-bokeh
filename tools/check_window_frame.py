"""Verify prepack window chrome and real editor lifecycle without packaging."""
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from PySide6.QtCore import QPoint, QPointF, Qt, QEvent
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
import main


def run():
    app = QApplication.instance() or QApplication([])
    output = ROOT / "tmp/window-frame-checks"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as folder, \
            patch.object(main, "_SETTINGS_PATH", Path(folder) / "settings.ini"), \
            patch.object(main.MainWindow, "log_device_profile"), \
            patch.dict(main.MOTION_DEFAULTS, {"enabled": False}):
        window = main.MainWindow()
        window.resize(1366, 800)
        window.show()
        app.processEvents()
        frame = window.window_frame
        try:
            for button in (frame.title_bar.minimize, frame.title_bar.maximize, frame.title_bar.close_button):
                button.clearFocus()
                button.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, False)
                button.setDown(False)
                normal = button.grab().toImage()
                button.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, True)
                hover = button.grab().toImage()
                button.setDown(True)
                pressed = button.grab().toImage()
                ratio = button.devicePixelRatioF()
                center = QPoint(normal.width()//2, normal.height()//2)
                assert normal.pixelColor(center) != hover.pixelColor(center)
                assert hover.pixelColor(center) != pressed.pixelColor(center)
                # A hover outline or changing dot diameter would modify pixels
                # outside the unchanged 12px circle. Only its fill may change.
                for y in range(normal.height()):
                    for x in range(normal.width()):
                        dx=(x+.5)/ratio-button.width()/2
                        dy=(y+.5)/ratio-button.height()/2
                        if dx*dx+dy*dy>7*7:
                            assert normal.pixel(x,y)==hover.pixel(x,y)==pressed.pixel(x,y)
                button.setDown(False)
                button.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, False)
            print("PASS: hover/press change fill grayscale only; no outline or changed diameter")
            assert [button.color.name() for button in (frame.title_bar.minimize, frame.title_bar.maximize,
                                                       frame.title_bar.close_button)] == ["#000000", "#001eff", "#ff3d36"]
            assert all(not button.text() for button in (frame.title_bar.minimize, frame.title_bar.maximize,
                                                         frame.title_bar.close_button))
            assert window.mask().isEmpty(), "No binary window mask may clip the antialiased compositor edge"
            if frame.native:
                native = frame.native
                if sys.getwindowsversion().build >= 22000:
                    assert native.corner_hresult == 0
                    assert native.read_corner_preference() == 2
                    print("PASS: Windows 11 DWM rounding accepted and read back; no binary mask")
                required = 0x00C00000 | 0x00040000 | 0x00080000 | 0x00020000 | 0x00010000
                assert native.get_style(int(window.winId()), -16) & required == required
                rect = native.types.RECT()
                native.user32.GetWindowRect(int(window.winId()), native.ctypes.byref(rect))
                scale = window.devicePixelRatioF()
                def hit(point):
                    msg = native.types.MSG()
                    msg.hWnd = int(window.winId())
                    msg.message = 0x0084
                    x, y = rect.left + round(point.x()*scale), rect.top + round(point.y()*scale)
                    msg.lParam = (x & 0xffff) | ((y & 0xffff) << 16)
                    return native.handle(native.ctypes.addressof(msg), frame)
                assert hit(QPoint(100, 16)) == 2  # native caption for Aero Snap
                assert hit(frame.title_bar.maximize.geometry().center()) == 1  # no native glyph overlay
                assert hit(frame.title_bar.close_button.geometry().center()) == 1
                assert hit(QPoint(2, 2)) == 13
                print("PASS: real HWND snap styles, native caption/resize and client-painted dot hit testing")
            print("PASS: three design dots, native outer corners and accessible controls")
            assert window.windowFlags() & Qt.WindowType.FramelessWindowHint
            assert window.menuWidget() is frame.title_bar
            assert frame.title_bar.isVisible()
            assert all(edge.isVisible() for edge in frame.edges)
            assert not hasattr(window, "photoshop_bridge")
            assert window.centralWidget().geometry().top() >= frame.title_bar.height()
            window.setWindowTitle("长标题测试 · " + "照片名称" * 80)
            app.processEvents()
            assert frame.title_bar.caption.toolTip() == window.windowTitle()
            assert frame.title_bar.close_button.geometry().right() < frame.title_bar.width()
            window.setWindowTitle(f"{main.APP_TITLE} v{main.APP_VERSION} · {main.APP_EDITION}")
            window.grab().save(str(output / "welcome-frameless.png"))
            print("PASS: frameless flags, title, controls, resize edges and no Photoshop service")

            frame.title_bar.maximize.click()
            app.processEvents()
            assert window.isMaximized()
            assert window.mask().isEmpty()
            if frame.native and frame.native.corner_hresult == 0:
                assert frame.native.read_corner_preference() == 1
            assert all(not edge.isVisible() for edge in frame.edges)
            assert frame.title_bar.maximize.toolTip() == "还原窗口"
            frame.title_bar.maximize.click()
            app.processEvents()
            assert not window.isMaximized()
            assert window.mask().isEmpty()
            if frame.native and frame.native.corner_hresult == 0:
                assert frame.native.read_corner_preference() == 2
            assert all(edge.isVisible() for edge in frame.edges)
            QTest.mouseDClick(frame.title_bar, Qt.MouseButton.LeftButton)
            app.processEvents()
            assert window.isMaximized()
            frame.title_bar.maximize.click()
            app.processEvents()
            frame.title_bar.minimize.click()
            app.processEvents()
            assert window.isMinimized()
            window.showNormal()
            app.processEvents()
            print("PASS: maximize/restore, double-click, minimize/restore")

            # Exercise fallback geometry at two corners, including the minimum size.
            for edge in (frame.edges[4], frame.edges[7]):
                initial = window.geometry()
                edge._origin = (QPoint(200, 200), initial)
                event = QMouseEvent(QEvent.Type.MouseMove, QPointF(0, 0), QPointF(140, 140),
                                    Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
                edge.mouseMoveEvent(event)
                app.processEvents()
                assert window.width() >= window.minimumSizeHint().width()
                assert window.height() >= window.minimumSizeHint().height()
                assert window.geometry() != initial
                edge._origin = None
            window.resize(1366, 800)
            window._enter_workspace("start")
            app.processEvents()
            assert window.is_workspace_active()
            assert frame.title_bar.isVisible()
            window.grab().save(str(output / "workspace-frameless.png"))
            floating = next(item for item in window.light_workspace.floating if item.title == "焦点景深")
            floating.detach()
            app.processEvents()
            dialog = floating.floating
            header = dialog.header
            assert header.caption.isHidden()
            assert len(dialog.window_frame.edges) == 8
            assert all(edge.isVisible() for edge in dialog.window_frame.edges)
            assert header.close_button.text() == ""
            header.minimize.click()
            app.processEvents()
            assert dialog.isMinimized() and floating.floating is dialog
            dialog.showNormal()
            header.maximize.click()
            app.processEvents()
            assert dialog.isMaximized() and floating.floating is dialog
            header.maximize.click()
            app.processEvents()
            assert not dialog.isMaximized() and floating.floating is dialog
            original_pos = dialog.pos()
            header._drag = QPoint(10,10)
            header.mouseMoveEvent(QMouseEvent(QEvent.Type.MouseMove,QPointF(0,0),
                QPointF(original_pos+QPoint(50,50)),Qt.MouseButton.NoButton,
                Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier))
            assert dialog.pos() == original_pos+QPoint(40,40)
            header._drag = None
            edge=dialog.window_frame.edges[7]
            old_size=dialog.size()
            edge._origin=(QPoint(200,200),dialog.geometry())
            edge.mouseMoveEvent(QMouseEvent(QEvent.Type.MouseMove,QPointF(0,0),QPointF(260,240),
                Qt.MouseButton.NoButton,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier))
            edge._origin=None
            app.processEvents()
            assert dialog.width()>old_size.width() and dialog.height()>old_size.height()
            dialog.grab().save(str(output / "floating-focus-dots.png"))
            header.close_button.click()
            app.processEvents()
            assert floating.floating is None
            assert window.light_workspace.focus.isVisible()
            for card in window.light_workspace.floating:
                card.detach()
                app.processEvents()
                assert card.floating.header.caption.isHidden(), card.title
                assert card.floating.header.close_button.isVisible(), card.title
                assert card.floating.header.maximize.isVisible(), card.title
                assert card.floating.header.minimize.isVisible(), card.title
                card.floating.header.close_button.click()
                app.processEvents()
                assert card.floating is None, card.title
            print("PASS: floating card drag/resize, dot minimize/maximize and red-dot return to workspace")
            window.toggle_theme()
            QTest.qWait(500)
            app.processEvents()
            assert window._theme_mode == "dark"
            assert frame.title_bar.isVisible()
            window.grab().save(str(output / "dark-frameless.png"))
            print("PASS: corner resizing, workspace navigation and dark theme")
        finally:
            frame.title_bar.close_button.click()
            app.processEvents()
            assert not window.isVisible()
            assert not window.render_worker.isRunning()
        print("PASS: custom close button stops the render worker")
    print("Screenshots:", output)


if __name__ == "__main__":
    run()
