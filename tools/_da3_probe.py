"""P0 探针：不装 moviepy/pycolmap/gsplat，直接用 DA3 的零件跑单图推理。

目的：验证在 numpy 2.x + Windows + CPU 上能不能跑通，以及要多久。
产物只写到 models/da3-probe/（已在 .gitignore 里）。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

ROOT = Path(__file__).resolve().parent.parent
PROBE = ROOT / "tools" / ".probe"
if str(PROBE) not in sys.path:
    sys.path.insert(0, str(PROBE))

import numpy as np
import torch
from PIL import Image
from safetensors.torch import load_file

from depth_anything_3.cfg import create_object, load_config
from depth_anything_3.registry import MODEL_REGISTRY
from depth_anything_3.utils.io.input_processor import InputProcessor
from depth_anything_3.utils.io.output_processor import OutputProcessor

REPO = "depth-anything/DA3-SMALL"
CACHE = ROOT / "models" / "da3-probe"
IMAGE = ROOT / "docs" / "images" / "修复后-对焦对比.png"


def download() -> Path:
    """带断点续传的下载：这条网络会把长连接掐断，必须 Range 续传。"""
    import requests

    CACHE.mkdir(parents=True, exist_ok=True)
    dest = CACHE / "model.safetensors"
    part = dest.with_suffix(".part")
    if dest.is_file() and dest.stat().st_size > 100 * 1024 * 1024:
        print(f"[download] cached {dest.stat().st_size / 1024 / 1024:.0f} MB")
        return dest

    endpoint = "https://hf-mirror.com"
    url = f"{endpoint}/{REPO}/resolve/main/model.safetensors"
    started = time.time()
    total = None
    for attempt in range(60):
        pos = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={pos}-"} if pos else {}
        try:
            with requests.get(url, headers=headers, stream=True, timeout=(15, 60)) as response:
                if response.status_code == 416:
                    break
                response.raise_for_status()
                if total is None:
                    if response.status_code == 206:
                        content_range = response.headers.get("Content-Range", "")
                        if "/" in content_range:
                            total = int(content_range.rsplit("/", 1)[1])
                    else:
                        total = int(response.headers.get("Content-Length") or 0) or None
                with open(part, "ab" if pos else "wb") as handle:
                    for chunk in response.iter_content(1024 * 256):
                        if chunk:
                            handle.write(chunk)
        except Exception as exc:
            print(f"  [retry {attempt}] {type(exc).__name__} at {pos / 1024 / 1024:.1f} MB")
        size = part.stat().st_size if part.exists() else 0
        if total and size >= total:
            break
        if attempt % 5 == 0:
            print(f"  {size / 1024 / 1024:.0f} / {(total or 0) / 1024 / 1024:.0f} MB")

    if total is None or (part.stat().st_size if part.exists() else 0) < total:
        raise RuntimeError(f"下载不完整：{part.stat().st_size if part.exists() else 0} / {total}")
    part.replace(dest)
    print(f"[download] {dest.stat().st_size / 1024 / 1024:.0f} MB in {time.time() - started:.1f}s")
    return dest


def run(weights: Path, process_res: int) -> None:
    print(f"\n[numpy] {np.__version__}  [torch] {torch.__version__}  [device] cpu")

    started = time.time()
    net = create_object(load_config(MODEL_REGISTRY["da3-small"]))
    # HF 检查点是从 DepthAnything3 包装类存的，键名带 "model." 前缀；
    # create_object 直接给的是内层 DepthAnything3Net，所以要剥掉一层。
    state = load_file(weights)
    state = {k[len("model.") :] if k.startswith("model.") else k: v for k, v in state.items()}
    missing, unexpected = net.load_state_dict(state, strict=False)
    if missing or unexpected:
        print(f"[load] missing={len(missing)} unexpected={len(unexpected)}")
        print(f"  missing[:6]={missing[:6]}")
    net.eval()
    print(f"[load] {time.time() - started:.1f}s")

    source = Image.open(IMAGE).convert("RGB")
    width, height = source.size
    cases = [
        ("full", source),
        ("left", source.crop((0, 0, width // 2, height))),
        ("ui", Image.open(ROOT / "design-system" / "baoyue-bokeh" / "preview-after.png").convert("RGB")),
    ]

    processor_in = InputProcessor()
    processor_out = OutputProcessor()

    for name, image in cases:
        started = time.time()
        # sequential=True：单张图不需要线程池；而且 Windows 上建 Pool 会开命名管道
        imgs, _, _ = processor_in(
            [image], None, None, process_res, "upper_bound_resize", sequential=True
        )
        prep = time.time() - started

        started = time.time()
        with torch.inference_mode():
            # 第 4 个参数必须给 list（api.py 里也把 None 转成了 []）
            raw = net(imgs[None].float(), None, None, [], False, False, "saddle_balanced")
        forward = time.time() - started

        pred = processor_out(raw)
        depth = pred.depth[0]
        span = float(depth.max() - depth.min())
        print(
            f"[{name:4s}] {image.size[0]}x{image.size[1]} -> {tuple(imgs.shape[-2:])} "
            f"prep={prep:.2f}s fwd={forward:.2f}s | depth {depth.shape} "
            f"min={float(depth.min()):.3f} max={float(depth.max()):.3f} span={span:.3f}"
        )
        if name == "full":
            print(f"[raw keys] {sorted(raw.keys())}")

        norm = (depth - depth.min()) / max(span, 1e-6)
        out = CACHE / f"depth_{name}_{process_res}.png"
        Image.fromarray((norm * 255).astype(np.uint8)).save(out)


def main() -> int:
    weights = download()
    run(weights, 504)
    run(weights, 756)
    return 0


if __name__ == "__main__":
    sys.exit(main())
