"""把品牌 logo 处理成可直接放进标题栏的透明 PNG。

源图是白底、单色黄（#F6D05E）的光圈图形，右侧还有一块几乎不可见的白色形状。
处理方式：
1. 用蓝色通道做"白底抠图"——白底 255、图形 94，线性反推 alpha，边缘不会出现白边；
2. 统一填成品牌黄 #F6D05D（与 tokens 的 accent 完全一致）；
3. 按 alpha 紧裁并留 1% 边距，避免贴图时上下不居中。

用法：
    .venv\\Scripts\\python.exe tools\\build_logo.py
产物：
    app/assets/brand/logo-mark.png
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "app" / "assets" / "brand" / "logo-source.png"
OUT = ROOT / "app" / "assets" / "brand" / "logo-mark.png"

BRAND = (0xF6, 0xD0, 0x5D)
# 源图里图形的蓝色通道值（白底为 255）
SHAPE_BLUE = 94


def main() -> int:
    if not SRC.is_file():
        print(f"找不到源图：{SRC}")
        return 1
    src = Image.open(SRC).convert("RGB")
    width, height = src.size

    alpha = Image.new("L", (width, height))
    src_px = src.load()
    alpha_px = alpha.load()
    for y in range(height):
        for x in range(width):
            blue = src_px[x, y][2]
            value = (255 - blue) / (255 - SHAPE_BLUE)
            alpha_px[x, y] = max(0, min(255, round(value * 255)))

    box = alpha.point(lambda v: 255 if v > 12 else 0).getbbox()
    if box is None:
        print("没有识别到图形，检查源图")
        return 1

    pad = max(2, round(max(box[2] - box[0], box[3] - box[1]) * 0.01))
    left, top = max(0, box[0] - pad), max(0, box[1] - pad)
    right, bottom = min(width, box[2] + pad), min(height, box[3] + pad)

    alpha = alpha.crop((left, top, right, bottom))
    mark = Image.new("RGBA", alpha.size, (*BRAND, 0))
    mark.putalpha(alpha)
    mark.save(OUT, "PNG")
    print(f"{OUT.relative_to(ROOT)}: {mark.size[0]}×{mark.size[1]}, 色值 #F6D05D")
    return 0


if __name__ == "__main__":
    sys.exit(main())
