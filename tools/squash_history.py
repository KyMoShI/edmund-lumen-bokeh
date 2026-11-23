"""把 29 个提交压成 4 个里程碑提交，源码目录树保持不变。

做法：每个里程碑直接用"当时那个提交的 tree 对象"，只改父链和提交信息。
tree 不变 ⇒ 每个里程碑都能 checkout 出和当年完全一致的文件，
最后一个里程碑的 tree 与压缩前的 HEAD tree 完全相同。

里程碑对应关系（tree 取自压缩前的真实提交）：
  33a311c  2026-10-19 18:50  初始版本
  f4626a6  2026-10-20 04:15  v1.1.0
  d4af2dd  2026-10-20 21:47  v1.1.1
  125a791  2026-10-21 00:41  接入 DA3（当前 HEAD 的 tree）

用法：
    .venv\\Scripts\\python.exe tools\\squash_history.py --dry-run
    .venv\\Scripts\\python.exe tools\\squash_history.py
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT / "github-upload"

NAME = "Moki_Makesi"
EMAIL = "48328991+KyMoShI@users.noreply.github.com"

# (提交信息, tree, 作者时间)
MILESTONES = [
    (
        "feat: 初始版本 —— 桌面大光圈模拟器\n\n"
        "- PySide6 桌面界面：导入图片、左右对比、虚化/光斑参数\n"
        "- OpenCV 实现的光圈 PSF 卷积虚化、景深与 Bokeh 叠加\n"
        "- 中心对焦 / 手动点击对焦，无需任何 AI 模型\n"
        "- 支持中文路径，PNG/JPEG 全分辨率导出\n",
        "33a311cdd9a0b783c2e1ed5610943c7aab5570cf",
        "2026-10-19T18:50:59+08:00",
    ),
    (
        "feat: v1.1.0 —— 球差与主体边缘、深度分辨率、配置记忆\n\n"
        "- 新增球差、主体边缘扩张/羽化、高光溢出补偿\n"
        "- 深度图分辨率可选（518 / 756 / 1036 / 1260）\n"
        "- 设置项持久化，后台渲染线程，AI 依赖可选安装与打包脚本\n"
        "- 导出支持格式/分辨率/抗锯齿/质量，含透明效果图层\n",
        "f4626a66eab83cbb18d9ebb35b7c6df1a693d694",
        "2026-10-20T04:15:58+08:00",
    ),
    (
        "chore: v1.1.1 —— 界面版本号、事件日志与交互打磨\n\n"
        "- 标题栏显示版本号与「诗人革命」标语\n"
        "- 处理进度提示、事件日志（参数/耗时）、设备档案\n"
        "- 效果范围高亮、直方图、预览缩放平移、Ctrl+Z/Y 撤销重做\n",
        "d4af2dd4d7a3d8cd7a1b68025b5be3f9fb1fbc87",
        "2026-10-20T21:47:02+08:00",
    ),
    (
        "feat: 接入 Depth Anything 3（DA3 Small / Base）\n\n"
        "- 新增 ModelSpec 注册表与双后端（transformers / da3），与 V2 并存\n"
        "- DA3 走薄适配层：配置→网络→safetensors→输入输出处理器，不依赖 moviepy/pycolmap\n"
        "- 统一深度方向（DA2 视差 vs DA3 深度），预览与焦点滑块语义一致\n"
        "- DA3 用 2%–98% 分位数归一化，V2 保持 min-max\n",
        "125a791f0b151360d40192684ddfaf0e19132a55",
        "2026-10-21T00:41:05+08:00",
    ),
]

TAGS = {"v1.1.0": 1, "v1.1.1": 2}  # tag 名 -> 里程碑下标


def git(*args: str, input_bytes: bytes | None = None, env: dict | None = None) -> bytes:
    return subprocess.run(
        ["git", "-C", str(REPO), *args],
        input=input_bytes,
        capture_output=True,
        check=True,
        env=env,
    ).stdout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    head = git("rev-parse", "HEAD").strip().decode()
    head_tree = git("rev-parse", "HEAD^{tree}").strip().decode()
    final_tree = MILESTONES[-1][1]

    if head_tree != final_tree:
        print(f"HEAD tree 与最后一个里程碑不一致，拒绝执行。\n  HEAD: {head_tree}\n  预期: {final_tree}")
        return 1
    if git("status", "--porcelain").strip():
        print("工作区不干净，先提交或 stash。")
        return 1

    print(f"当前 HEAD {head[:7]}，tree {head_tree[:7]}")
    print(f"将压缩为 {len(MILESTONES)} 个提交\n")

    if args.dry_run:
        for message, tree, date in MILESTONES:
            print(f"  {date[:16]}  {tree[:7]}  {message.splitlines()[0]}")
        return 0

    git("update-ref", "refs/backup/pre-squash/main", head)

    parent = None
    created: list[str] = []
    for message, tree, date in MILESTONES:
        env = dict(os.environ)
        env.update(
            GIT_AUTHOR_NAME=NAME,
            GIT_AUTHOR_EMAIL=EMAIL,
            GIT_AUTHOR_DATE=date,
            GIT_COMMITTER_NAME=NAME,
            GIT_COMMITTER_EMAIL=EMAIL,
            GIT_COMMITTER_DATE=date,
        )
        cmd = ["commit-tree", tree]
        if parent:
            cmd += ["-p", parent]
        parent = git(*cmd, input_bytes=message.encode("utf-8"), env=env).strip().decode()
        created.append(parent)
        print(f"  {parent[:7]}  {tree[:7]}  {message.splitlines()[0]}")

    git("update-ref", "refs/heads/main", created[-1])
    git("update-ref", "refs/heads/release/v1.1.0", created[1])
    for tag, index in TAGS.items():
        git("update-ref", f"refs/tags/{tag}", created[index])

    print(f"\n完成：main -> {created[-1][:7]}")
    print("检查：")
    print("  git log --oneline")
    print(f"  git rev-parse 'HEAD^{{tree}}'   # 应为 {final_tree[:7]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
