"""对比度体检：把 tokens.py 里的语义色两两组合算 WCAG 对比度。

改任何颜色后跑一次，正文低于 4.5:1 直接非零退出，方便以后接进 CI。

用法：
    .venv\\Scripts\\python.exe tools\\contrast.py
    .venv\\Scripts\\python.exe tools\\contrast.py --all   # 打印全部组合
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

from theme import tokens  # noqa: E402

# (前景 token, 背景 token, 最低要求, 用途)
CHECKS = [
    ("color.text.primary", "color.bg.root", 4.5, "正文/标题"),
    ("color.text.primary", "color.bg.panel", 4.5, "面板内正文"),
    ("color.text.primary", "color.bg.control", 4.5, "控件内文字"),
    ("color.text.secondary", "color.bg.root", 4.5, "状态栏/次级信息"),
    ("color.text.muted", "color.bg.root", 4.5, "辅助说明 11px"),
    ("color.text.muted", "color.bg.panel", 4.5, "面板内辅助说明"),
    ("color.text.muted", "color.bg.control", 4.5, "控件内辅助说明"),
    ("color.accent", "color.bg.root", 4.5, "强调色文字（标题）"),
    ("color.text.warm", "color.bg.root", 4.5, "文字型操作（暗底橙字）"),
    ("color.text.on.warm", "color.accent", 4.5, "黄底椭圆按钮上的橙字"),
    ("color.on.accent", "color.accent", 4.5, "主按钮文字"),  # 琥珀上的黑字
]


def _lin(channel: int) -> float:
    value = channel / 255
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def luminance(hex_color: str) -> float:
    raw = hex_color.lstrip("#")
    r, g, b = (int(raw[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def ratio(fg: str, bg: str) -> float:
    a, b = luminance(fg), luminance(bg)
    high, low = max(a, b), min(a, b)
    return (high + 0.05) / (low + 0.05)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="额外打印所有前景/背景组合")
    args = parser.parse_args()

    colors = tokens.SEMANTIC
    failures = 0
    print(f"{'前景':<22}{'背景':<22}{'实测':>7}  {'要求':>5}  {'结论':<6} 用途")
    print("-" * 88)
    for fg_key, bg_key, minimum, usage in CHECKS:
        value = ratio(colors[fg_key], colors[bg_key])
        ok = value >= minimum
        failures += 0 if ok else 1
        print(
            f"{fg_key:<22}{bg_key:<22}{value:>7.2f}  {minimum:>5.1f}  "
            f"{'PASS' if ok else 'FAIL':<6} {usage}"
        )

    if args.all:
        print("\n全部组合：")
        fg_keys = [k for k in colors if k.startswith("color.text") or k.startswith("color.accent")]
        bg_keys = [k for k in colors if k.startswith("color.bg")]
        for fg_key in fg_keys:
            row = "  ".join(f"{ratio(colors[fg_key], colors[bg_key]):5.2f}" for bg_key in bg_keys)
            print(f"{fg_key:<22}{row}")

    print("-" * 88)
    print(f"不达标：{failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
