# design-system/

本目录是"设计决策"的落点：先由 ui-ux-pro-max 生成，再由项目方覆盖并编译成 Qt 令牌。
**代码里的颜色以 `app/theme/tokens.py` 为准，这里的文档是它的依据与解释。**

```
design-system/
└── baoyue-bokeh/
    ├── MASTER.md          # 设计源真相（数据库输出 + 方向 B 覆盖 + 令牌表）
    ├── pages/             # 页面级覆盖（目前为空，需要时再加）
    ├── preview-before.png # 换肤前的真实主窗口
    └── preview-after.png  # 换肤后的真实主窗口（P0 验证用）
```

## 生成 / 再生成

```powershell
# 1) 安装 skill（已装过则跳过；更新时重跑）
npx ui-ux-pro-max-cli init --ai claude

# 2) 生成设计系统并持久化（会写 MASTER.md）
.\.venv\Scripts\python.exe .claude\skills\ui-ux-pro-max\scripts\search.py `
  "professional photo editor desktop tool neutral dark precision" `
  --design-system -p "Baoyue Bokeh" --variance 3 --density 8 --motion 3 `
  --persist --output-dir .

# 3) 单点查询（改配色 / 字体 / 交互规则时用）
.\.venv\Scripts\python.exe .claude\skills\ui-ux-pro-max\scripts\search.py `
  "focus visible keyboard slider value" --domain ux -n 3
```

> `--persist` 在 `MASTER.md` 已存在时会跳过写入（防止覆盖人工决策）。
> 真要重生成，先备份或显式加 `--force`。

## 验证

```powershell
.\.venv\Scripts\python.exe tools\contrast.py        # 对比度体检，0 项不达标才放行
.\.venv\Scripts\python.exe tools\preview_theme.py   # 真实主窗口截图到本目录
```

## 分发

打发布包时建议排除（它们是设计与构建资产，不是运行依赖）：

- `.claude/`（3.6 MB，skill 数据）
- `design-system/`（截图与文档）
- `tools/`（构建脚本）

`app/theme/`、`app/assets/fonts/` 是运行依赖，必须保留。
