"""Temporary native-window preview with isolated settings; closes after 2 min."""
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
import main

app = QApplication([])
with tempfile.TemporaryDirectory(prefix="bokeh-frame-preview-") as folder, \
        patch.object(main, "_SETTINGS_PATH", Path(folder) / "settings.ini"), \
        patch.object(main.MainWindow, "log_device_profile"), \
        patch.dict(main.MOTION_DEFAULTS, {"enabled": False}):
    window = main.MainWindow()
    window.setWindowTitle("圆角验收 · 伯爵大光圈 1.2.5")
    window.resize(1000, 730)
    window.move(100, 70)
    window.show()
    QTimer.singleShot(120000, window.close)
    app.exec()
