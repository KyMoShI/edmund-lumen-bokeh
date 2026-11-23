"""主题加载：把 `tokens.py` 编译成 QPalette + QSS，并在启动时注册内置字体。

为什么基础配色走 QPalette 而不是全塞进 QSS：
- QSS 的 `QWidget { background }` 会级联到所有子控件，嵌套面板永远分不出层；
- QPalette 只管"默认底色/字色"，QSS 只管边框、圆角、间距与状态，职责清晰；
- 原生控件（菜单、滚动区、对话框）自动跟着变暗，不会出现白底弹窗。

用法（P1 里接到 main.py）::

    from theme import apply_theme
    apply_theme(app)          # 必须在 QApplication 建好之后、MainWindow 之前
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QGraphicsDropShadowEffect

from . import tokens


def _app_dir() -> Path:
    """源码运行时是 `app/`；PyInstaller 打包后是 `<_MEIPASS>/app/`。

    打包时数据文件按 `app/theme`、`app/assets` 的结构放进去，
    这样源码与 exe 两套路径只差一个根目录。
    """
    if getattr(sys, "frozen", False):
        bundle = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        return bundle / "app"
    return Path(__file__).resolve().parent.parent


APP_DIR = _app_dir()
PKG_DIR = APP_DIR / "theme"
FONT_DIR = APP_DIR / "assets" / "fonts"
ICON_DIR = PKG_DIR / "icons"
QSS_TEMPLATE = PKG_DIR / "app.qss.tmpl"


def asset(*parts: str) -> Path:
    """取 `app/assets/` 下的资源，打包后同样可用。"""
    return APP_DIR.joinpath("assets", *parts)

# 内置字体没有覆盖到的生僻字（人名/路径）回落到系统字体
FALLBACK_FAMILIES = ["Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "sans-serif"]

_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_.]+)\s*\}\}")


# --------------------------------------------------------------------------
# 字体
# --------------------------------------------------------------------------

def load_bundled_fonts() -> str:
    """注册 app/assets/fonts 下的字体，返回实际可用的家族名。"""
    if not FONT_DIR.is_dir():
        return tokens.FONT["font.family"]
    selected = None
    for path in sorted(FONT_DIR.glob("*.ttf")):
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id == -1:
            continue
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families and path.name.startswith("SarasaGothicSC"):
            selected = families[0]
    return selected or tokens.FONT["font.family"]


# --------------------------------------------------------------------------
# 调色板
# --------------------------------------------------------------------------

def build_palette(mode: str = "light") -> QPalette:
    c = tokens.context(mode)
    palette = QPalette()
    role = QPalette.ColorRole
    group = QPalette.ColorGroup

    palette.setColor(role.Window, QColor(c["color.bg.root"]))
    palette.setColor(role.WindowText, QColor(c["color.text.primary"]))
    palette.setColor(role.Base, QColor(c["color.bg.panel"]))
    palette.setColor(role.AlternateBase, QColor(c["color.bg.control"]))
    palette.setColor(role.ToolTipBase, QColor(c["color.bg.tooltip"]))
    palette.setColor(role.ToolTipText, QColor(c["color.text.on.dark"]))
    palette.setColor(role.Text, QColor(c["color.text.primary"]))
    palette.setColor(role.Button, QColor(c["color.bg.panel"]))
    palette.setColor(role.ButtonText, QColor(c["color.text.primary"]))
    palette.setColor(role.BrightText, QColor(c["color.text.primary"]))
    palette.setColor(role.Highlight, QColor(c["color.accent"]))
    palette.setColor(role.HighlightedText, QColor(c["color.on.accent"]))
    palette.setColor(role.PlaceholderText, QColor(c["color.text.disabled"]))
    palette.setColor(role.Link, QColor(c["color.alt"]))

    for disabled_role in (
        role.WindowText, role.Text, role.ButtonText, role.Highlight,
        role.HighlightedText, role.PlaceholderText,
    ):
        palette.setColor(group.Disabled, disabled_role, QColor(c["color.text.disabled"]))
    return palette


# --------------------------------------------------------------------------
# QSS
# --------------------------------------------------------------------------

def _asset_values(mode: str = "light") -> dict[str, str]:
    # PNG 而非 SVG：QSS 的 image: url(*.svg) 依赖 imageformats/qsvg 插件，
    # 部分 PySide6 安装没有该插件，会静默失效。
    return {
        "asset.chevron": (ICON_DIR / ("chevron-down-dark.png" if mode == "dark" else "chevron-down.png")).as_posix(),
        "asset.check": (ICON_DIR / ("check-dark.png" if mode == "dark" else "check.png")).as_posix(),
    }


def render_qss(mode: str = "light") -> str:
    """渲染 QSS 模板；有未定义的占位符就直接报错，不要静默产出坏样式。"""
    values = tokens.context(mode)
    values.update(_asset_values(mode))
    text = QSS_TEMPLATE.read_text(encoding="utf-8")
    missing = sorted({m.group(1) for m in _PLACEHOLDER.finditer(text)} - values.keys())
    if missing:
        raise KeyError(f"QSS 模板里有未定义的令牌：{', '.join(missing)}")
    rendered = _PLACEHOLDER.sub(lambda m: values[m.group(1)], text)
    if mode == "light":
        rendered += """
        #PreviewSurface { background: #ffffff; border: 1px solid #ff4b49; border-radius: 0px; }
        #PreviewSurface[empty="true"] { background: rgba(255, 255, 255, 150); }
        #PreviewSurface[drag="true"] { border: 2px dashed #d92d20; }
        QLabel[role="previewTag"] { background: #ef3b38; color: white; padding: 3px 9px; font-weight: 600; }
        QLabel#EditorialMark { color: #1734e8; font-size: 86px; font-weight: 700; }
        QFrame#EditorialBrand { background: #202020; }
        QLabel#EditorialBrandText { color: white; font-size: 32px; font-weight: 700; }
        QLabel#EditorialDiamond { color: white; font-size: 40px; font-weight: 700; }
        #MainSplitter::handle:horizontal { background: transparent; }
        QGroupBox { border: 1px solid #ffaaa6; border-radius: 10px; }
        QPushButton[variant="primary"], QPushButton[variant="warm"] { border-radius: 7px; }
        #ControlsScroll { background: #ffffff; border: 1px solid #ff7773; }
        #OpenImageButton, #CompareImageButton, #ExportImageButton { min-width: 125px; min-height: 50px; padding-left: 22px; padding-right: 22px; font-size: 18px; font-weight: 700; }
        """
    else:
        rendered += """
        QLabel#EditorialMark { color: #F6D05D; font-size: 40px; font-weight: 700; }
        QFrame#EditorialBrand { background: #F6D05D; }
        QLabel#EditorialBrandText, QLabel#EditorialDiamond { color: #0A0A0A; font-size: 18px; font-weight: 700; }
        """
    return rendered


def add_card_shadow(widget, blur: int = 28, dy: int = 6, alpha: int = 26) -> None:
    """给卡片加柔和投影（参考图里的悬浮卡片感）。

    QSS 没有 box-shadow，只能用 QGraphicsDropShadowEffect。只用在固定数量的
    卡片上，不要给列表里每一行加，否则重绘会明显变卡。
    """
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, dy)
    color = QColor("#8C7B6B")
    color.setAlpha(alpha)
    effect.setColor(color)
    widget.setGraphicsEffect(effect)


def apply_theme(app, mode: str = "light") -> str:
    """给 QApplication 装上主题，返回实际使用的字体家族名。"""
    app.setStyle("Fusion")
    family = load_bundled_fonts()

    font = QFont()
    font.setFamilies([family, *FALLBACK_FAMILIES])
    font.setPixelSize(int(tokens.FONT["font.size.body"]))
    font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    app.setFont(font)

    app.setPalette(build_palette(mode))
    app.setStyleSheet(render_qss(mode))
    return family
