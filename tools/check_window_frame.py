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
            assert all(not edge.isVisible() for edge in frame.edges)
            assert frame.title_bar.maximize.toolTip() == "还原窗口"
            frame.title_bar.maximize.click()
            app.processEvents()
            assert not window.isMaximized()
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
