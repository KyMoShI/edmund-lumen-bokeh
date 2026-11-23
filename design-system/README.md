# 界面设计文档

## 规范入口

| 要查的内容 | 文档 | 适用范围 |
| --- | --- | --- |
| 桌面主题与公共组件 | [桌面界面规范](baoyue-bokeh/MASTER.md) | 比较浅色、黑夜两种工作区，定位共享状态与历史设计 |
| 页面与图层 | [浅色模式界面规范](baoyue-bokeh/LIGHT-RESET.md) | 欢迎页、工作区、导出完成页和二级窗口的构图、锚点与缩放 |
| 字体与控件尺寸 | [视觉梯度规范](baoyue-bokeh/VISUAL-SCALE.md) · [设计参数清单](baoyue-bokeh/light-reset.scale.json) | 字重、字阶、边框、圆角、留白和对齐。清单分别记录原稿测量与运行值 |
| 页面导航与交互决策 | [浅色界面交互流程图](baoyue-bokeh/INTERACTION-FLOW.md) | 已填写的 17 项原始决定与其后明确修订的行为 |
| 模型与处理分支 | [功能分支参考](baoyue-bokeh/FUNCTION-REFERENCE.md) | 深度估算、预览、历史和导出。参数取值范围由[用户说明](../README.md#参数范围与精确输入)维护 |
| 流程图可视化编辑 | [可编辑流程图](baoyue-bokeh/工作区流程图.drawio) · [编辑说明](baoyue-bokeh/流程图编辑说明.md) | 节点、连线和页面编辑。交互决策的文字依据见 Markdown 流程图 |
| 设计源文件 | [设计文件检查记录](baoyue-bokeh/设计文件检查记录.md) | PDF 成稿、Illustrator 兼容层差异、画板外素材及文件指纹 |

## 页面样例

| 页面 | 对照图 |
| --- | --- |
| 欢迎页 | [品牌标题](baoyue-bokeh/pages/welcome.png) · [提示语](baoyue-bokeh/pages/welcome-prompt.png) · [窄窗口](baoyue-bokeh/pages/welcome-portrait.png) |
| 编辑工作区 | [空工作区](baoyue-bokeh/pages/workspace.png) · [已导入图片](baoyue-bokeh/pages/workspace-photo.png) · [卡片高度与预览分区](baoyue-bokeh/pages/workspace-adjustable.png) · [焦点参数展开](baoyue-bokeh/pages/workspace-focus.png) |
| 图像统计 | [直方图与拍摄信息](baoyue-bokeh/pages/workspace-histogram.png) · [亮度及三个颜色通道](baoyue-bokeh/pages/histogram-channels.png) |
| 导出与二级窗口 | [导出设置](baoyue-bokeh/pages/export-settings.png) · [导出完成](baoyue-bokeh/pages/export-completion.png) · [动画设置](baoyue-bokeh/pages/animation-settings.png) · [窄窗口帮助](baoyue-bokeh/pages/project-help.png) · [错误提示](baoyue-bokeh/pages/message-error.png) · [浮动操作示意](baoyue-bokeh/浮动窗口说明.md) |

设计稿位于本地项目根目录。可分发的图层、锚点和控件边界见[欢迎页分层](../app/assets/welcome/scene.json)、[工作区布局](../app/assets/workspace/layout.json)、[背景缩放策略](../app/assets/workspace/background.json)和[完成页布局](../app/assets/completion/layout.json)。
