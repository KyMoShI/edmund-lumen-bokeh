# 更新日志

本文件记录每个发布版本的变化。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循语义化版本；发版时打同名标签 `vX.Y.Z`（版本号单一来源在 `app/version.py`）。

## [1.1.0] - 2026-10-20

### 新增

- **球差调节**：按「径向能量再分配」模拟欠校正（边缘亮环、硬朗）与过校正（中心亮、柔和）散景；卷积核归一化，总光量守恒。
- **猫眼压缩可调**：0＝所有光斑都是正圆（默认），调大模拟画面边缘的机械暗角把光斑横向压扁。
- **主体边缘调整**：清晰主体可收缩/扩张并加羽化，用来缓解深度模型在轮廓处留下的一圈光晕。
- **AI 深度推理分辨率可选**：518 / 756 / 1036 / 1260（绕开 transformers pipeline，直接把 `size` 传给 processor）。
- **配置记忆**：全部设置连同窗口大小写入 `settings.ini`，下次打开自动继承。
- **AI 深度模型导入**：可选本地 HF 格式模型文件夹，或填仓库 ID / 链接；本地没有时自动联网下载（hf-mirror → huggingface.co，断点续传），导入的模型会被记住。
- **控制面板可滚动**：控件再多也不会被窗口截断。

### 修复

- **圆形光斑边缘呈多边形棱角**：虚化卷积核改用解析距离场生成真圆，绘制光斑采样点 48 → 128。
- **控制面板标签被横向裁切**：下拉框最小宽度不再由内容决定（`minimumContentsLength=8`），横向策略改为按需。
- **界面卡死（“未响应”）**：`engine.process()` 移到独立 `RenderWorker` 线程；预览请求合并、导出排队，导出等待用嵌套事件循环 + 60 s 超时兜底。
- **打包 exe 报 `No module named 'torch'`**：`--with-ai` 打包时补 `--collect-submodules transformers` 与 `torch/tokenizers/safetensors/huggingface_hub` 隐藏导入。

### 变更

- 渲染在后台线程执行：全尺寸预览与导出期间界面保持响应，效果图延迟出现。

## [1.0.0] - 2026-10-19

### 新增

- 首个版本：Windows 桌面大光圈模拟工具（PySide6）。
- 中心对焦 / 手动点击对焦 / AI 深度估算（Depth Anything V2 Small·Base·Large）。
- 按光圈形状 PSF 卷积的背景虚化（圆形 / 5–9 边形），圆角散景光斑、亮边、猫眼压缩、加性叠加。
- 深度图导入 / 导出（PNG·TIFF 16 位、JPEG 8 位），浅景深随导出分辨率缩放。
- 导出设置：内容（合成图 / 透明大光圈图层）、格式、分辨率、光斑抗锯齿、JPEG 质量。
- 黄黑高对比界面，设计令牌集中在 `app/theme/`；支持中文路径、EXIF 方向自动校正。
- 一键启动脚本与 PyInstaller 打包（轻量版）。

[未发布]: https://github.com/KyMoShI/edmund-lumen-bokeh/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/KyMoShI/edmund-lumen-bokeh/releases/tag/v1.1.0
[1.0.0]: https://github.com/KyMoShI/edmund-lumen-bokeh/releases/tag/v1.0.0
