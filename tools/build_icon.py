"""用品牌 logo 生成 Windows 图标（多尺寸 .ico）。

用途：
- exe 的图标（PyInstaller --icon）
- 窗口 / 任务栏图标（QIcon 直接读同一个 .ico）

源图是 224×230 的透明 PNG，先补成正方形再缩放，
否则 Windows 会把图标拉伸变形。

用法：
    .venv\\Scripts\\python.exe tools\\build_icon.py
产物：
    app/assets/brand/app.ico
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "app" / "assets" / "brand" / "logo-mark.png"
OUT = ROOT / "app" / "assets" / "brand" / "app.ico"

SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def main() -> int:
    if not SRC.is_file():
        print(f"找不到 logo：{SRC}（先跑 tools/build_logo.py）")
        return 1

    mark = Image.open(SRC).convert("RGBA")
    side = max(mark.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(mark, ((side - mark.width) // 2, (side - mark.height) // 2), mark)

    # 留 6% 内边距：Windows 图标本体偏满，贴边会显得臃肿
    inner = int(side * 0.88)
    padded = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    scaled = square.resize((inner, inner), Image.LANCZOS)
    offset = (side - inner) // 2
    padded.paste(scaled, (offset, offset), scaled)

    canvas = padded.resize((256, 256), Image.LANCZOS)
    canvas.save(OUT, format="ICO", sizes=SIZES)
    print(f"{OUT.relative_to(ROOT)}: {', '.join(f'{w}×{h}' for w, h in SIZES)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
