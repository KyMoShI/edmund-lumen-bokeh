"""截图当前真实界面（含主题），用于评审与回归。

主窗口本身在 `__main__` 里才装主题，直接 import 拿到的是裸 Qt 控件；
所以这里按 `__main__` 的顺序来：先 apply_theme，再建窗口，再截图。

`preview-before.png` 是改造前的历史存档，本脚本不会覆盖它，
只负责重写 `preview-after.png` 和拼接 `preview-compare.png`。

用法：
    .venv\\Scripts\\python.exe tools\\preview_theme.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
APP_DIR = ROOT / "app"
OUT_DIR = ROOT / "design-system" / "baoyue-bokeh"
sys.path.insert(0, str(APP_DIR))

from PySide6.QtWidgets import QApplication  # noqa: E402

from theme import apply_theme  # noqa: E402


def main() -> int:
    from main import MainWindow  # 延迟导入，确保 QApplication 已建

    app = QApplication(sys.argv)
    app.setApplicationName("Baoyue Bokeh Preview")
    apply_theme(app)  # offscreen 下系统字体不可见，必须用内置字体

    window = MainWindow()
    window.resize(1280, 800)
    window.show()
    for _ in range(4):
        app.processEvents()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    after = OUT_DIR / "preview-after.png"
    window.grab().save(str(after))

    before = OUT_DIR / "preview-before.png"
    if before.is_file():
        from PIL import Image

        top = Image.open(before).convert("RGB")
        bottom = Image.open(after).convert("RGB")
        canvas = Image.new("RGB", (1280, top.height + 10 + bottom.height), (0xF6, 0xD0, 0x5D))
        canvas.paste(top, (0, 0))
        canvas.paste(bottom, (0, top.height + 10))
        canvas.save(OUT_DIR / "preview-compare.png")

    print(f"after: {after}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
