"""把系统里的 Noto Sans SC 变量字体裁成应用内置的两档静态字重。

为什么不在运行时直接用系统的 Noto Sans SC / 微软雅黑：
- 不同机器上的微软雅黑版本差异大，行高与字宽会让排好的版式跑位；
- 变量字体在 Qt 6.6 之前只能取默认实例，字重不可控。

产物（OFL 1.1，可随应用分发）：
    app/assets/fonts/BaoyueUI-Regular.ttf    wght 400
    app/assets/fonts/BaoyueUI-SemiBold.ttf   wght 600
    app/assets/fonts/OFL.txt                 许可证全文（必须随字体一起分发）

用法：
    .venv\\Scripts\\python.exe tools\\build_font.py
依赖：fonttools（仅构建期需要，运行时不依赖）
"""

from __future__ import annotations

import sys
from pathlib import Path

from fontTools.subset import Options, Subsetter
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "app" / "assets" / "fonts"
FAMILY = "Baoyue UI"
WEIGHTS = ((400, "Regular", "Regular"), (600, "SemiBold", "SemiBold"))

# 变量字体源文件：优先用系统里已装的 Noto Sans SC；找不到就明确提示怎么获取。
FONT_CANDIDATES = [
    Path(r"C:\Windows\Fonts\NotoSansSC-VF.ttf"),
    Path(r"C:\Windows\Fonts\NotoSansSC[wght].ttf"),
    Path(r"C:\Windows\Fonts\Noto Sans SC (TrueType).ttf"),
    Path.home() / "Downloads" / "NotoSansSC-VF.ttf",
]
SRC = next((p for p in FONT_CANDIDATES if p.is_file()), FONT_CANDIDATES[0])

# UI 里会出现的标点与符号；GB2312 一级字库覆盖 3755 个常用汉字
PUNCT = "，。、；：？！（）【】《》〈〉「」『』…—～·“”‘’￥％＋－×÷≤≥→←↑↓○●◆◇■□★☆№"
CJK_PUNCT = "　、。〃々〆〇《》「」『』【】〔〕〖〗〘〙〚〛〜〝〞〟〰〾〿–—‘’‛“”„‟…‧﹏"


def project_chars() -> set[str]:
    """项目里真正出现过的字符，避免以后加文案就掉字。"""
    chars: set[str] = set()
    targets = list((ROOT / "app").rglob("*.py"))
    targets += list((ROOT / "docs").glob("*.md"))
    targets.append(ROOT / "README.md")
    for path in targets:
        if path.is_file():
            try:
                chars.update(path.read_text(encoding="utf-8"))
            except (UnicodeDecodeError, OSError):
                continue
    return chars


def gb2312_level1() -> set[str]:
    chars: set[str] = set()
    for hi in range(0xB0, 0xD8):
        for lo in range(0xA1, 0xFF):
            try:
                chars.add(bytes([hi, lo]).decode("gb2312"))
            except UnicodeDecodeError:
                continue
    return chars


def build_charset() -> str:
    chars = {chr(c) for c in range(0x20, 0x7F)}
    chars |= set(PUNCT) | set(CJK_PUNCT)
    chars |= gb2312_level1()
    chars |= {c for c in project_chars() if c.isprintable()}
    return "".join(sorted(chars))


def rename(font: TTFont, style: str) -> None:
    """改成独立家族名，避免和系统已装的 Noto Sans SC 撞车。"""
    full = f"{FAMILY} {style}" if style != "Regular" else FAMILY
    ps = f"{FAMILY.replace(' ', '')}-{style}"
    name = font["name"]
    for name_id, value in ((1, FAMILY), (3, f"{ps};1.0"), (4, full), (6, ps), (16, FAMILY), (17, style)):
        for rec in list(name.names):
            if rec.nameID == name_id:
                name.removeNames(nameID=name_id)
        name.setName(value, name_id, 3, 1, 0x409)


def main() -> int:
    if not SRC.is_file():
        print("找不到 Noto Sans SC 变量字体。")
        print("任选一种方式：")
        print("  1. 装一份 Noto Sans SC（Windows 字体设置里安装），它会出现在 C:\\Windows\\Fonts；")
        print("  2. 从 https://fonts.google.com/noto/specimen/Noto+Sans+SC 下载后放到")
        print(f"     {FONT_CANDIDATES[3]}；")
        print(f"  3. 修改本文件顶部的 FONT_CANDIDATES 指向你的字体文件。")
        print("\n找不到源字体也会影响不了仓库：app/assets/fonts/ 里已有构建好的 BaoyueUI-*.ttf。")
        return 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    text = build_charset()
    print(f"目标字符集：{len(text)} 个字符")

    base = TTFont(SRC)
    options = Options()
    options.layout_features = ["*"]
    options.name_IDs = ["*"]
    options.notdef_outline = True
    options.recalc_bounds = True
    options.drop_tables += ["DSIG"]
    subsetter = Subsetter(options=options)
    subsetter.populate(text=text)
    subsetter.subset(base)

    tmp = OUT_DIR / "_subset-vf.ttf"
    base.save(tmp)
    base.close()
    print(f"子集完成：{tmp.stat().st_size / 1024:.0f} KB")

    for weight, style, axis_name in WEIGHTS:
        font = TTFont(tmp)
        instantiateVariableFont(font, {"wght": weight}, inplace=True, updateFontNames=False)
        rename(font, style)
        out = OUT_DIR / f"BaoyueUI-{style}.ttf"
        font.save(out)
        font.close()
        print(f"{out.name}: {out.stat().st_size / 1024:.0f} KB")

    tmp.unlink(missing_ok=True)
    print("完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
