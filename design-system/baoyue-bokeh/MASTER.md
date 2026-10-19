# Design System Master File

> **LOGIC:** When building a specific page, first check `design-system/pages/[page-name].md`.
> If that file exists, its rules **override** this Master file.
> If not, strictly follow the rules below.

---

**Project:** Edmund Lumen Bokeh (伯爵大光圈模拟)
**Generated:** 2026-10-19 16:52:32
**Category:** Photo Editor & Filters
**Design Dials:** Variance 3/10 (Centered / Minimal) | Motion 3/10 (Subtle) | Density 8/10 (Dense / Dashboard)

---

## Global Rules

### Color Palette

| Role | Hex | CSS Variable |
|------|-----|--------------|
| Primary | `#7C3AED` | `--color-primary` |
| On Primary | `#FFFFFF` | `--color-on-primary` |
| Secondary | `#6366F1` | `--color-secondary` |
| On Secondary | `#000000` | `--color-on-secondary` |
| Accent/CTA | `#0891B2` | `--color-accent` |
| On Accent/CTA | `#000000` | `--color-on-accent` |
| Background | `#0F172A` | `--color-background` |
| Foreground | `#FFFFFF` | `--color-foreground` |
| Card | `#192134` | `--color-card` |
| Card Foreground | `#FFFFFF` | `--color-card-foreground` |
| Muted | `#171939` | `--color-muted` |
| Muted Foreground | `#94A3B8` | `--color-muted-foreground` |
| Border | `rgba(255,255,255,0.08)` | `--color-border` |
| Destructive | `#DC2626` | `--color-destructive` |
| On Destructive | `#FFFFFF` | `--color-on-destructive` |
| Ring | `#7C3AED` | `--color-ring` |

**Color Notes:** Editor violet + filter cyan on dark

### Typography

- **Heading Font:** Inter
- **Body Font:** Inter
- **Mood:** dark, cinematic, technical, precision, clean, premium, developer, professional, high-end utility
- **Google Fonts:** [Inter + Inter](https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap)

**CSS Import:**
```css
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
```

### Spacing Variables

*Density: 8/10 — Dense / Dashboard*

| Token | Value | Usage |
|-------|-------|-------|
| `--space-xs` | `2px` / `0.125rem` | Tight gaps |
| `--space-sm` | `4px` / `0.25rem` | Icon gaps, inline spacing |
| `--space-md` | `8px` / `0.5rem` | Standard padding |
| `--space-lg` | `12px` / `0.75rem` | Section padding |
| `--space-xl` | `16px` / `1rem` | Large gaps |
| `--space-2xl` | `24px` / `1.5rem` | Section margins |
| `--space-3xl` | `32px` / `2rem` | Hero padding |

### Shadow Depths

| Level | Value | Usage |
|-------|-------|-------|
| `--shadow-sm` | `0 1px 2px rgba(0,0,0,0.05)` | Subtle lift |
| `--shadow-md` | `0 4px 6px rgba(0,0,0,0.1)` | Cards, buttons |
| `--shadow-lg` | `0 10px 15px rgba(0,0,0,0.1)` | Modals, dropdowns |
| `--shadow-xl` | `0 20px 25px rgba(0,0,0,0.15)` | Hero images, featured cards |

---

## Component Specs

### Buttons

```css
/* Primary Button */
.btn-primary {
  background: #0891B2;
  color: white;
  padding: 12px 24px;
  border-radius: 8px;
  font-weight: 600;
  transition: all 200ms ease;
  cursor: pointer;
}

.btn-primary:hover {
  opacity: 0.9;
  transform: translateY(-1px);
}

/* Secondary Button */
.btn-secondary {
  background: transparent;
  color: #7C3AED;
  border: 2px solid #7C3AED;
  padding: 12px 24px;
  border-radius: 8px;
  font-weight: 600;
  transition: all 200ms ease;
  cursor: pointer;
}
```

### Cards

```css
.card {
  background: #0F172A;
  border-radius: 12px;
  padding: 24px;
  box-shadow: var(--shadow-md);
  transition: all 200ms ease;
  cursor: pointer;
}

.card:hover {
  box-shadow: var(--shadow-lg);
  transform: translateY(-2px);
}
```

### Inputs

```css
.input {
  padding: 12px 16px;
  border: 1px solid #E2E8F0;
  border-radius: 8px;
  font-size: 16px;
  transition: border-color 200ms ease;
}

.input:focus {
  border-color: #7C3AED;
  outline: none;
  box-shadow: 0 0 0 3px #7C3AED20;
}
```

### Modals

```css
.modal-overlay {
  background: rgba(0, 0, 0, 0.5);
  backdrop-filter: blur(4px);
}

.modal {
  background: white;
  border-radius: 16px;
  padding: 32px;
  box-shadow: var(--shadow-xl);
  max-width: 500px;
  width: 90%;
}
```

---

## Style Guidelines

**Style:** Minimalism & Swiss Style

**Keywords:** Clean, simple, spacious, functional, white space, high contrast, geometric, sans-serif, grid-based, essential

**Best For:** Enterprise apps, dashboards, documentation sites, SaaS platforms, professional tools

**Key Effects:** Subtle hover (200-250ms), smooth transitions, sharp shadows if any, clear type hierarchy, fast loading

### Page Pattern

**Pattern Name:** Product Demo + Features

- **Conversion Strategy:** Use an interactive demo only when it explains value better than static media. Provide captions, transcript, visible play/pause controls, and a non-video fallback; do not autoplay under reduced motion. Pause media when offscreen or hidden and keep the final product state available as static content.
- **CTA Placement:** Video center + CTA right/bottom
- **Section Order:** Hero > Product video/mockup (center) > Feature breakdown per section > Comparison (optional) > CTA

---

## Motion

**Scroll Reveal** (Subtle) — Trigger: scroll (viewport enter) | Duration: 300-400ms | Easing: `power1.out`

```js
gsap.from(el, { opacity: 0, y: 12, duration: 0.35, ease: 'power1.out', scrollTrigger: { trigger: el, start: 'top 90%', toggleActions: 'play none none reverse' } });
```

**Framework notes:** Requires the ScrollTrigger plugin registered once via gsap.registerPlugin(ScrollTrigger); Use matchMedia('(prefers-reduced-motion: reduce)') to skip non-essential motion and render the final state immediately

- ✅ Keep the y offset small (8-16px) so it reads as a fade, not a slide
- ❌ Don't reveal below-the-fold content needed for SEO/crawlers as invisible-by-default without a no-JS fallback
- ⚡ toggleActions 'play none none reverse' avoids re-triggering on every scroll direction change

---

## Anti-Patterns (Do NOT Use)

- ❌ Excessive decoration
- ❌ Pure white backgrounds

### Additional Forbidden Patterns

- ❌ **Emojis as icons** — Use SVG icons (Heroicons, Lucide, Simple Icons)
- ❌ **Missing cursor:pointer** — All clickable elements must have cursor:pointer
- ❌ **Layout-shifting hovers** — Avoid scale transforms that shift layout
- ❌ **Low contrast text** — Maintain 4.5:1 minimum contrast ratio
- ❌ **Instant state changes** — Always use transitions (150-300ms)
- ❌ **Invisible focus states** — Focus states must be visible for a11y

---

## Pre-Delivery Checklist

Before delivering any UI code, verify:

- [ ] No emojis used as icons (use SVG instead)
- [ ] All icons from consistent icon set (Heroicons/Lucide)
- [ ] `cursor-pointer` on all clickable elements
- [ ] Hover states with smooth transitions (150-300ms)
- [ ] Light mode: text contrast 4.5:1 minimum
- [ ] Focus states visible for keyboard navigation
- [ ] `prefers-reduced-motion` respected
- [ ] Responsive: 375px, 768px, 1024px, 1440px
- [ ] No content hidden behind fixed navbars
- [ ] No horizontal scroll on mobile

---

# 项目覆盖：方向 B（保留黄黑高对比）

> 本节由项目方在生成后追加，**优先级高于上面的数据库输出**。
> 覆盖原因：产品已有稳定视觉身份（黄 #FFD400 + 近黑），本次目标是"精修执行质量"，
> 不是换品牌。数据库给的是"新项目从零起"的建议，与产品决策冲突的部分按本节执行。

## 1. 被否决的数据库建议

| 数据库建议 | 决策 | 理由 |
|---|---|---|
| Primary `#7C3AED`（编辑紫）/ Accent `#0891B2`（滤镜青） | ❌ 否决 | 与既有黄黑身份冲突；紫色在色彩管理界面里会干扰对画面色偏的判断 |
| Background `#0F172A`（午夜蓝） | ❌ 否决 | 深蓝底会给照片加冷色偏，看图工具应使用中性灰黑 |
| 字体 Inter / Inter | ⚠️ 部分采纳 | 拉丁字形沿用 Inter 的骨架，但中文必须自带字体；见第 3 节 |
| Scroll Reveal / GSAP 动效 | ❌ 不适用 | 桌面工具的动效是状态反馈（hover/press/focus），不是滚动叙事 |
| Landing Page Pattern（Hero/CTA/对比区） | ❌ 不适用 | 这是桌面单窗口工具，没有落地页结构 |
| `cursor-pointer` / 响应式断点 375–1440px | ❌ 不适用 | Web 专有；桌面用 `Qt.PointingHandCursor` 与窗口 resize 策略 |

## 2. 保留并执行的数据库建议

- **风格**：Minimalism & Swiss Style — 克制、网格化、单一强调色、无装饰。
- **Density 8/10**：控件高度 28px、行距 8/12px 网格，符合专业工具的信息密度。
- **Motion 3/10**：只用 120–160ms 的状态过渡，不做位移动画。
- **Accessibility**：正文 4.5:1、可见焦点环、键盘可达（对应 UX 规则 `Focus States`，Severity: High）。
- **图标纪律**：禁止 emoji 当图标，统一线性图标（本次只用到 chevron / check 两枚）。
- **反模式**：过度装饰、纯白背景、布局抖动式 hover、瞬时状态切换。
- **栈级建议（借 JavaFX 类比）**：主题要当"底座"而不是"整套设计系统"；
  不要从零重写每个标准控件；密度、表单、校验规则仍要自己定义。

## 3. 最终令牌（已落地在 `app/theme/tokens.py`）

| 语义 | 值 | 用途 |
|---|---|---|
| `color.bg.root` | `#0A0A0A` | 窗口底（品牌黑，保留） |
| `color.bg.panel` | `#101010` | 卡片/分组底（新增一层，解决"全屏一个色"） |
| `color.bg.control` | `#141414` | 按钮、下拉、输入框底 |
| `color.bg.control.hover` | `#1F1F1F` | hover 底 |
| `color.bg.canvas` | `#050505` | 图片画布（比窗口更暗，形成"装裱"边界） |
| `color.border` | `#333333` | 默认描边（沿用现有值） |
| `color.border.subtle` | `#262626` | 分隔线、卡片描边 |
| `color.text.primary` | `#F5F5F5` | 正文（18.16:1） |
| `color.text.secondary` | `#B8B8B8` | 状态栏/次级（9.98:1） |
| `color.text.muted` | `#9A9A9A` | 11px 辅助说明（7.04:1） |
| `color.accent` | `#F6D05D` | 品牌黄（与 logo 同色，13.29:1） |
| `color.accent.hover` | `#FFDF8F` | 主操作 hover（15.28:1） |
| `color.accent.active` | `#D9B44C` | 主操作按下（9.98:1） |
| `color.on.accent` | `#0A0A0A` | 黄底上的文字（13.29:1） |
| `color.text.warm` | `#FF9F43` | 暗底上的橙字（9.70:1） |
| `color.text.on.warm` | `#9A3412` | 黄底椭圆按钮上的橙字（4.91:1；亮橙在黄上只有 1.37:1，不可用） |
| 字阶 | 11 / 12 / 13 / 14 / 20 px | 辅助 / 正文 / 数值 / 分组标题 / 窗口标题 |
| 间距 | 2 / 4 / 8 / 12 / 16 / 24 / 32 | 4 的倍数网格 |
| 圆角 | 4 / 8 / 12 | 小控件 / 按钮输入 / 卡片 |

**强调色配额**：整屏只允许出现在 —— ① 窗口标题 ② 主操作按钮 ③ 焦点环 ④ 滑块已填充段 ⑤ 当前值。
分组标题不再用琥珀（已改为 `text.primary`），避免强调色通胀。

**色彩标准化（v2）**：全项目黄色统一为 `#F6D05D`，来源是 logo 的实际取色（源图 `#F6D05E`）。
旧值 `#FFD400` 及其 hover `#FFE45C` / pressed `#E0BA00` 全部废弃；
`app/` 下不再存在任何字面量黄色，所有黄色只能从 `tokens.AMBER` 取。

**品牌标**：`app/assets/brand/logo-mark.png`（224×230，透明底，已抠白底并统一为 `#F6D05D`），
由 `tools/build_logo.py` 从 `logo-source.png` 生成；界面里按 24px 逻辑尺寸、2× 渲染。

**导入按钮（暖色实心）**：黄底圆角矩形（`radius.md` = 8px）、无边框、深橙字 `#9A3412`。
亮橙 `#FF9F43` 在黄底上只有 1.37:1，不可用；深橙 4.91:1，达标。
无边框按钮少了上下各 1px 描边，纵向 padding 补 1px（5px）才能和其他按钮同为 38px 高。

## 4. 字体

- 打包：Noto Sans SC（OFL 1.1）子集 + 静态实例，改名 `Baoyue UI`，
  Regular/SemiBold 各约 1.2MB，合计 2.4MB（预算 8–15MB）。
- 构建：`tools/build_font.py`；许可：`app/assets/fonts/OFL.txt`。
- 回落：缺字时依次回落到 Microsoft YaHei UI / Segoe UI。

## 5. 验收

- `tools/contrast.py`：9 组前景/背景全部 ≥ 4.5:1（当前 0 项不达标）。
- `tools/preview_theme.py`：真实主窗口套用新主题后的截图，用于前后对比。
- 数据库清单里的 Web 条目（断点、CLS、prefers-reduced-motion）在桌面端不适用，
  由 `Qt` 侧的等价项替代（窗口最小尺寸、`QPropertyAnimation` 的时长上限、系统"减少动效"设置）。

