"""用 PyInstaller 把程序打成 Windows 可执行文件。

默认打「目录版」（onedir）：exe 旁边带 `_internal/`，启动快，
模型可以放在 exe 同级的 `models/` 里被直接读到。

用法：
    .venv\\Scripts\\python.exe tools\\build_exe.py                # 目录版（推荐）
    .venv\\Scripts\\python.exe tools\\build_exe.py --onefile      # 单文件版（启动慢几秒）
    .venv\\Scripts\\python.exe tools\\build_exe.py --with-ai      # 把 torch/transformers 也打进去（很大）

产物：
    dist/伯爵大光圈模拟/伯爵大光圈模拟.exe
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_NAME = "伯爵大光圈模拟"
ICON = ROOT / "app" / "assets" / "brand" / "app.ico"
ENTRY = ROOT / "app" / "main.py"

# 这些包只在 AI 深度估算里懒加载；不打包时程序会提示"依赖缺失"，
# 其余功能（中心对焦、手动焦点、导入深度图）全部可用。
HEAVY_EXCLUDES = ["torch", "torchvision", "torchaudio", "transformers", "accelerate"]
COMMON_EXCLUDES = ["tkinter", "matplotlib", "scipy", "pytest", "IPython", "notebook"]


def clean_caches() -> None:
    """打包前清掉 __pycache__，否则会被当成资源一起塞进 _internal/app。"""
    for cache in ROOT.rglob("__pycache__"):
        if "node_modules" in cache.parts or ".venv" in cache.parts:
            continue
        shutil.rmtree(cache, ignore_errors=True)


def build(onefile: bool, with_ai: bool, clean: bool) -> Path:
    clean_caches()
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--windowed",
        "--name", APP_NAME,
        "--icon", str(ICON),
        "--add-data", f"{ROOT / 'app' / 'theme'};app/theme",
        "--add-data", f"{ROOT / 'app' / 'assets'};app/assets",
        "--paths", str(ROOT / "app"),
    ]
    cmd.append("--clean" if clean else "--log-level=WARN")
    if onefile:
        cmd.append("--onefile")
    for module in COMMON_EXCLUDES:
        cmd += ["--exclude-module", module]
    if not with_ai:
        for module in HEAVY_EXCLUDES:
            cmd += ["--exclude-module", module]
    else:
        # The AI imports are lazy (inside functions) and transformers resolves
        # model classes dynamically at load time, so collect the whole package
        # instead of trusting PyInstaller's static analysis alone. Without this
        # a frozen build still fails with "No module named 'torch'".
        cmd += ["--collect-submodules", "transformers"]
        for module in ("torch", "tokenizers", "safetensors", "huggingface_hub"):
            cmd += ["--hidden-import", module]
    cmd.append(str(ENTRY))

    print("执行：", " ".join(f'"{c}"' if " " in c else c for c in cmd))
    subprocess.run(cmd, cwd=ROOT, check=True)

    dist = ROOT / "dist" / APP_NAME
    if onefile:
        dist = ROOT / "dist" / f"{APP_NAME}.exe"
    return dist


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--onefile", action="store_true", help="打成单个 exe（启动更慢）")
    parser.add_argument("--with-ai", action="store_true", help="包含 torch/transformers（体积 +1GB 以上）")
    parser.add_argument("--clean", action="store_true", help="清空 build/ 后重新打包")
    args = parser.parse_args()

    if not ICON.is_file():
        print("缺少图标，先运行 tools/build_icon.py")
        return 1

    target = build(args.onefile, args.with_ai, args.clean)
    if not target.exists():
        print("打包失败：找不到产物", target)
        return 1

    if target.is_dir():
        exe = target / f"{APP_NAME}.exe"
        size = sum(f.stat().st_size for f in target.rglob("*") if f.is_file())
        print(f"\n产物目录：{target}")
        print(f"可执行文件：{exe}")
        print(f"总大小：{size / 1024 / 1024:.0f} MB")
    else:
        print(f"\n产物：{target}")
        print(f"大小：{target.stat().st_size / 1024 / 1024:.0f} MB")

    print("\n提示：把 exe（目录版是整个文件夹）拷走即可运行；"
          "深度模型会下载到 exe 同级的 models/。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
