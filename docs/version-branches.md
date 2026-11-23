# 1.2.5 分支与打包入口

根目录是当前打包前源码，`tools/build_exe.py` 的入口为根目录 `app/main.py`。该版本采用无边框主窗口，不包含 Photoshop 联动服务或插件源码。此前已生成的 exe 和安装程序尚未重建。

Git 仓库位于 `github-upload/.git`。以下两个分支使用各自工作目录，不切换或清理 `github-upload` 中现有 main 分支的未提交改动。

| Git 分支 | 工作目录 | 内容 |
| --- | --- | --- |
| `feature/v1.2.5-photoshop` | `branches/v1.2.5-photoshop` | 隔离保存的 1.2.5 PS 联动原型，包含 UXP 插件、桥接服务和测试，保留隔离时的系统边框 |
| `release/v1.2.5-prepack` | `branches/v1.2.5-prepack` | 不带 PS 联动的打包前版本，包含无边框主窗口 |

PS 联动版可运行其目录中的 `启动PS联动.bat`。在本工作区内会优先使用分支自己的 `.venv`，否则复用根目录 `.venv`。现已安装至 Photoshop 的插件仍保留在 Photoshop 插件目录。

无边框主窗口去掉系统标题栏和系统边框，提供随主题变化的自绘顶部操作区：拖动移动、双击最大化/还原，以及最小化、最大化、关闭按钮。四条边和四角保留缩放操作，优先使用 Qt 原生系统移动/缩放，回退实现限制最小窗口尺寸。最大化后关闭边缘缩放。

验证入口：`python tools/check_window_frame.py`。截图输出到 `tmp/window-frame-checks/`。当前工作只更新打包前源码，不自动覆盖旧安装包。
