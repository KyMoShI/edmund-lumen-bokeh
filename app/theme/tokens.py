"""设计令牌 —— 「伯爵大光圈」UI 的唯一色源。

当前方向：浅色纸面 + 红蓝点缀（参考「RE:VOLUTION」式的编辑排版）。
结构仍然是 Minimalism / Swiss：细线、圆角、留白、单一强调色。

使用规则
--------
1. QSS 模板只能写 ``{{token}}``，禁止出现字面量 ``#RRGGBB``；
2. 新颜色先进 ``PAPER`` / ``INK`` / ``RED`` / ``BLUE``，再在 ``SEMANTIC`` 里给语义名；
3. 改色后必须重跑 ``tools/contrast.py``，正文对比度 ≥ 4.5:1；
4. 这里的值是按 100% DPI 的逻辑像素，Qt 会按 devicePixelRatio 自动缩放。
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# 原始色（primitive）
# --------------------------------------------------------------------------

PAPER = {
    "p000": "#FFFFFF",  # 卡片、控件底
    "p050": "#FBFAF9",  # 控件底（比卡片再暗一点）
    "p100": "#F7F5F3",  # 窗口底：暖白纸面
    "p150": "#F2EFEC",  # hover 底
    "p200": "#EDE8E4",  # 画布 / 按下底
    "p300": "#E4DED9",  # 弱分隔线
    "p400": "#D3CBC5",  # 默认描边
    "p500": "#B7AEA7",  # 强描边 / hover
}

INK = {
    "k900": "#141414",  # 主文字 / 关键按钮底
    "k800": "#2B2826",  # 黑底按钮 hover
    "k700": "#4A4643",  # 次级文字
    "k500": "#6E6864",  # 辅助说明
    "k400": "#A9A29C",  # 禁用（WCAG 对 inactive 组件豁免）
}

RED = {
    "r700": "#A81E16",  # 按下
    "r600": "#C62828",  # hover
    "r500": "#D92D20",  # 主强调（白字在其上 4.83:1）
    "r400": "#B42318",  # 浅底上的红字
    "r050": "#FDECEA",  # 浅红底
}

BLUE = {
    "b600": "#1D4ED8",  # 次强调 / 焦点环
    "b500": "#2563EB",
    "b050": "#E8EEFD",
}

# --------------------------------------------------------------------------
# 语义色（semantic）—— 唯一被 QSS 引用的层
# --------------------------------------------------------------------------

SEMANTIC = {
    # 背景
    "color.bg.root": PAPER["p100"],
    "color.bg.panel": PAPER["p000"],
    "color.bg.control": PAPER["p050"],
    "color.bg.control.hover": PAPER["p150"],
    "color.bg.control.active": PAPER["p200"],
    "color.bg.canvas": PAPER["p200"],
    "color.bg.tooltip": INK["k900"],
    "color.bg.soft": PAPER["p150"],
    # 描边
    "color.border.subtle": PAPER["p300"],
    "color.border": PAPER["p400"],
    "color.border.hover": PAPER["p500"],
    "color.border.focus": BLUE["b600"],
    # 文字
    "color.text.primary": INK["k900"],
    "color.text.secondary": INK["k700"],
    "color.text.muted": INK["k500"],
    "color.text.disabled": INK["k400"],
    "color.text.on.dark": PAPER["p000"],
    # 强调（红）
    "color.accent": RED["r500"],
    "color.accent.hover": RED["r600"],
    "color.accent.active": RED["r700"],
    "color.accent.ink": RED["r400"],
    "color.accent.soft": RED["r050"],
    "color.on.accent": PAPER["p000"],
    # 次强调（蓝）
    "color.alt": BLUE["b600"],
    "color.alt.soft": BLUE["b050"],
    # 关键按钮（黑底白字）
    "color.solid": INK["k900"],
    "color.solid.hover": INK["k800"],
    "color.solid.active": "#000000",
    "color.on.solid": PAPER["p000"],
    # 焦点
    "color.focus.ring": BLUE["b600"],
    "color.focus.ring.inner": PAPER["p000"],
}


# 延续旧版黄黑主题，并补齐当前模板新增的语义色。
DARK = {
    "color.bg.root": "#0A0A0A", "color.bg.panel": "#101010",
    "color.bg.control": "#141414", "color.bg.control.hover": "#1F1F1F",
    "color.bg.control.active": "#181818", "color.bg.canvas": "#050505",
    "color.bg.tooltip": "#262626", "color.bg.soft": "#1F1F1F",
    "color.border.subtle": "#262626", "color.border": "#454545",
    "color.border.hover": "#6B6B6B", "color.border.focus": "#F6D05D",
    "color.text.primary": "#F5F5F5", "color.text.secondary": "#B8B8B8",
    "color.text.muted": "#B0B0B0", "color.text.disabled": "#777777",
    "color.text.on.dark": "#FFFFFF",
    "color.accent": "#F6D05D", "color.accent.hover": "#FFDF8F",
    "color.accent.active": "#D9B44C", "color.accent.ink": "#F6D05D",
    "color.accent.soft": "#332C19", "color.on.accent": "#0A0A0A",
    "color.alt": "#F6D05D", "color.alt.soft": "#332C19",
    "color.solid": "#F6D05D", "color.solid.hover": "#FFDF8F",
    "color.solid.active": "#D9B44C", "color.on.solid": "#0A0A0A",
    "color.focus.ring": "#F6D05D", "color.focus.ring.inner": "#0A0A0A",
}
# 已实测的对比度（tools/contrast.py 复算，sRGB / WCAG 2.1）
CONTRAST = {
    ("text.primary", "bg.root"): 16.94,
    ("text.primary", "bg.panel"): 17.6,
    ("text.secondary", "bg.root"): 8.59,
    ("text.muted", "bg.root"): 5.05,
    ("text.muted", "bg.panel"): 5.3,
    ("on.accent", "accent"): 4.83,
    ("accent.ink", "bg.panel"): 6.57,
    ("alt", "bg.panel"): 6.70,
}

# --------------------------------------------------------------------------
# 间距 / 圆角 / 字阶 / 控件尺寸 / 动效
# --------------------------------------------------------------------------

SPACE = {
    "space.1": 2,
    "space.2": 4,
    "space.3": 8,
    "space.4": 12,
    "space.5": 16,
    "space.6": 24,
    "space.7": 32,
}

RADIUS = {
    "radius.sm": 8,     # 勾选、滑块手柄
    "radius.md": 12,    # 按钮、输入框、下拉
    "radius.lg": 18,    # 卡片、画布
    "radius.pill": 999,  # 主按钮胶囊
}

FONT = {
    "font.family": "Baoyue UI",
    "font.mono": "Cascadia Mono",
    "font.size.caption": 11,
    "font.size.body": 12,
    "font.size.bodyLg": 13,
    "font.size.section": 14,
    "font.size.title": 22,
    "font.weight.regular": 400,
    "font.weight.medium": 500,
    "font.weight.semibold": 600,
    "font.weight.bold": 700,
}

CONTROL = {
    "control.height.sm": 26,
    "control.height": 32,
    "control.height.lg": 38,
    "control.padding.x": 12,
    "control.slider.groove": 4,
    "control.slider.handle": 16,
    "control.focus.width": 2,
}

MOTION = {
    "motion.fast": 120,
    "motion.base": 160,
    "motion.easing": "OutCubic",
}


def context(mode: str = "light") -> dict[str, str]:
    """把令牌摊平成 ``{"color.bg.root": "#F7F5F3", ...}`` 供 QSS 模板替换。"""
    values: dict[str, object] = {}
    for group in (SEMANTIC, SPACE, RADIUS, FONT, CONTROL, MOTION):
        values.update(group)
    if mode == "dark":
        values.update(DARK)
    return {key: str(value) for key, value in values.items()}
