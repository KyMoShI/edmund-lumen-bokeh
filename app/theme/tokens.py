"""设计令牌 —— 「伯爵大光圈」UI 的唯一色源。

来源：`design-system/baoyue-bokeh/MASTER.md`（ui-ux-pro-max 生成）
覆盖：设计方向 B —— 保留黄黑高对比，只精修执行质量。
      数据库给出的 "Photo Editor & Filters" 调色板是紫/青，被产品决策否决；
      结构建议（Minimalism & Swiss、Density 8、Motion 3、无 emoji 图标、
      可见焦点、正文 4.5:1）全部保留。

使用规则
--------
1. QSS 模板只能写 ``{{token}}``，禁止出现字面量 ``#RRGGBB``；
2. 新颜色先进 ``NEUTRAL`` / ``AMBER``，再在 ``SEMANTIC`` 里给语义名；
3. 改色后必须重跑 ``tools/contrast.py``，正文对比度 ≥ 4.5:1；
4. 这里的值是按 100% DPI 的逻辑像素，Qt 会按 devicePixelRatio 自动缩放。
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# 原始色（primitive）—— 只描述"是什么颜色"，不描述"用在哪"
# --------------------------------------------------------------------------

NEUTRAL = {
    "n950": "#050505",  # 画布：比根背景再暗一档，让照片有"装裱"边界
    "n900": "#0A0A0A",  # 根背景 / 品牌黑
    "n850": "#0D0D0D",
    "n800": "#101010",  # 面板、分组底
    "n750": "#141414",  # 控件底
    "n700": "#181818",  # 控件底（抬高一层，用于选中/滑轨槽）
    "n600": "#1F1F1F",  # hover 底
    "n500": "#262626",  # 弱分隔线
    "n400": "#333333",  # 默认描边
    "n300": "#454545",  # 强描边 / hover 描边
    "n200": "#6B6B6B",  # 禁用文字（WCAG 对 inactive 组件豁免）
    "n100": "#9A9A9A",  # 三级文字
    "n050": "#B8B8B8",  # 二级文字
    "n000": "#F5F5F5",  # 主文字
}

AMBER = {
    "a300": "#FFDF8F",  # hover
    "a400": "#F6D05D",  # 品牌强调色（与 logo 完全一致，全项目唯一黄）
    "a500": "#D9B44C",  # pressed
}

# 暖橙：给"导入图片"这类文字型操作使用。
# 注意：亮橙 (#FF9F43) 放在品牌黄上只有 1.37:1，读不清；
# 黄底上的橙字必须用深橙 #9A3412（4.91:1），暗底上才用亮橙。
ORANGE = {
    "o400": "#FF9F43",  # 暗底上的橙字
    "o300": "#FFB877",  # 暗底 hover
    "o500": "#E8892E",  # 暗底 pressed
    "o700": "#9A3412",  # 黄底上的橙字（唯一可用）
}

# --------------------------------------------------------------------------
# 语义色（semantic）—— 唯一被 QSS 引用的层
# --------------------------------------------------------------------------

SEMANTIC = {
    # 背景
    "color.bg.root": NEUTRAL["n900"],
    "color.bg.panel": NEUTRAL["n800"],
    "color.bg.control": NEUTRAL["n750"],
    "color.bg.control.hover": NEUTRAL["n600"],
    "color.bg.control.active": NEUTRAL["n700"],
    "color.bg.canvas": NEUTRAL["n950"],
    "color.bg.tooltip": NEUTRAL["n700"],
    # 描边
    "color.border.subtle": NEUTRAL["n500"],
    "color.border": NEUTRAL["n400"],
    "color.border.hover": NEUTRAL["n300"],
    "color.border.focus": AMBER["a400"],
    # 文字
    "color.text.primary": NEUTRAL["n000"],
    "color.text.secondary": NEUTRAL["n050"],
    "color.text.muted": NEUTRAL["n100"],
    "color.text.disabled": NEUTRAL["n200"],
    # 暖色文字（暗底 / 黄底两套，不可混用）
    "color.text.warm": ORANGE["o400"],
    "color.text.warm.hover": ORANGE["o300"],
    "color.text.warm.active": ORANGE["o500"],
    "color.text.on.warm": ORANGE["o700"],
    # 强调
    "color.accent": AMBER["a400"],
    "color.accent.hover": AMBER["a300"],
    "color.accent.active": AMBER["a500"],
    "color.on.accent": NEUTRAL["n900"],
    # 焦点环（QSS 只能靠 border 模拟，真正的外发光留给自绘）
    "color.focus.ring": AMBER["a400"],
    "color.focus.ring.inner": NEUTRAL["n900"],
}

# 已实测的对比度（tools/contrast.py 复算，sRGB / WCAG 2.1）
CONTRAST = {
    ("text.primary", "bg.root"): 18.16,
    ("text.secondary", "bg.root"): 9.98,
    ("text.muted", "bg.root"): 7.04,
    ("text.muted", "bg.panel"): 6.71,
    ("accent", "bg.root"): 13.29,
    ("on.accent", "accent"): 13.29,
    ("accent.hover", "bg.root"): 15.28,
    ("text.primary", "bg.control"): 16.60,
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
    "radius.sm": 4,
    "radius.md": 8,
    "radius.lg": 12,
}

FONT = {
    # 打包字体见 tools/build_font.py；加载失败时回落到系统字体
    "font.family": "Baoyue UI",
    "font.mono": "Cascadia Mono",
    "font.size.caption": 11,  # 辅助说明（11px 是中文下限，不许再小）
    "font.size.body": 12,     # 正文、表单标签
    "font.size.bodyLg": 13,   # 数值、重要标签
    "font.size.section": 14,  # 分组标题
    "font.size.title": 20,    # 窗口标题
    "font.weight.regular": 400,
    "font.weight.medium": 500,
    "font.weight.semibold": 600,
    "font.weight.bold": 700,
}

CONTROL = {
    "control.height.sm": 24,
    "control.height": 28,
    "control.height.lg": 32,
    "control.padding.x": 10,
    "control.slider.groove": 4,
    "control.slider.handle": 14,
    "control.focus.width": 2,
}

MOTION = {
    "motion.fast": 120,   # hover / press
    "motion.base": 160,   # 面板展开、状态切换
    "motion.easing": "OutCubic",
}


def context() -> dict[str, str]:
    """把令牌摊平成 ``{"color.bg.root": "#0A0A0A", ...}`` 供 QSS 模板替换。"""
    values: dict[str, object] = {}
    for group in (SEMANTIC, SPACE, RADIUS, FONT, CONTROL, MOTION):
        values.update(group)
    return {key: str(value) for key, value in values.items()}
