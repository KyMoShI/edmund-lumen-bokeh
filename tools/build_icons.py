"""生成 QSS 用的小图标（PNG）。

为什么不用 SVG：当前 PySide6 安装里没有 `imageformats/qsvg` 插件，
QSS 的 `image: url(*.svg)` 会静默失败（表现为勾选框只剩一块纯色）。
PNG 不依赖插件，跨机器最稳。

为什么用 QPainter 而不是 Pillow：需要抗锯齿和圆角线帽，Pillow 画线没有 AA。

用法：
    .venv\\Scripts\\python.exe tools\\build_icons.py
产物：
    app/theme/icons/chevron-down.png   (30×30 @3x → 逻辑 10×10)
    app/theme/icons/check.png          (36×36 @3x → 逻辑 12×12)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "app" / "theme" / "icons"
sys.path.insert(0, str(ROOT / "app"))

from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen  # noqa: E402

SCALE = 3


def draw(name: str, size: float, color: str, width: float, points: list[tuple[float, float]]) -> None:
    px = int(round(size * SCALE))
    image = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(QColor(color))
    pen.setWidthF(width * SCALE)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    path = [QPointF(x * SCALE, y * SCALE) for x, y in points]
    for start, end in zip(path, path[1:]):
        painter.drawLine(start, end)
    painter.end()
    out = OUT_DIR / f"{name}.png"
    image.save(str(out), "PNG")
    print(f"{out.name}: {px}×{px}")


def main() -> int:
    _app = QGuiApplication(sys.argv)  # QPainter 需要 GUI 应用实例
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    draw(
        "chevron-down",
        size=10,
        color="#9A9A9A",
        width=1.4,
        points=[(2.2, 3.8), (5.0, 6.6), (7.8, 3.8)],
    )
    draw(
        "check",
        size=12,
        color="#0A0A0A",
        width=1.8,
        points=[(2.4, 6.2), (4.8, 8.6), (9.6, 3.4)],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
