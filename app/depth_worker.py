import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal


def _app_root() -> Path:
    """源码运行时是项目根；打包成 exe 后是 exe 所在目录（模型放在 exe 旁边）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


# Keep the Hugging Face model cache in the project's models/ folder so a fresh
# copy of the app is self-contained and does not depend on the user profile.
_CACHE_DIR = _app_root() / "models"
os.environ.setdefault("HF_HOME", str(_CACHE_DIR))

# Models offered in the UI. The first one is bundled with the project.
MODEL_CHOICES = [
    ("Depth Anything V2 Small", "depth-anything/Depth-Anything-V2-Small-hf", "约 95 MB"),
    ("Depth Anything V2 Base", "depth-anything/Depth-Anything-V2-Base-hf", "约 390 MB"),
    ("Depth Anything V2 Large", "depth-anything/Depth-Anything-V2-Large-hf", "约 1.3 GB"),
]
DEFAULT_MODEL = MODEL_CHOICES[0][1]

_MODEL_FILES = ("config.json", "preprocessor_config.json", "model.safetensors")
# hf-mirror is usually reachable from mainland China; huggingface.co is the fallback.
_ENDPOINTS = ("https://hf-mirror.com", "https://huggingface.co")


def model_label(repo_id):
    for name, rid, _size in MODEL_CHOICES:
        if rid == repo_id:
            return name
    return repo_id


def _repo_slug(repo_id):
    return repo_id.replace("/", "--")


def model_dir(repo_id):
    return _CACHE_DIR / _repo_slug(repo_id)


def _is_complete(path):
    config = path / "config.json"
    weights = path / "model.safetensors"
    return (
        config.is_file() and config.stat().st_size > 10
        and weights.is_file() and weights.stat().st_size > 1_000_000
    )


def _legacy_snapshot(repo_id):
    """Find a model in the standard Hugging Face cache layout."""
    base = _CACHE_DIR / "hub" / ("models--" + _repo_slug(repo_id))
    snapshots = base / "snapshots"
    if not snapshots.is_dir():
        return None
    candidates = []
    ref = base / "refs" / "main"
    if ref.is_file():
        candidates.append(snapshots / ref.read_text(encoding="utf-8").strip())
    candidates.extend(sorted(snapshots.iterdir()))
    for candidate in candidates:
        if candidate.is_dir() and _is_complete(candidate):
            return candidate
    return None


def resolve_model_path(repo_id):
    """Return a local model directory, or None when it still needs downloading."""
    local = model_dir(repo_id)
    if _is_complete(local):
        return local
    return _legacy_snapshot(repo_id)


def _format_size(num_bytes):
    if num_bytes >= 1024 * 1024 * 1024:
        return f"{num_bytes / (1024 ** 3):.1f} GB"
    if num_bytes >= 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.0f} MB"
    return f"{num_bytes / 1024:.0f} KB"


def _fetch_file(repo_id, filename, dest, progress, should_stop):
    """Download one file with Range resume; returns False when cancelled."""
    import requests

    part = dest.with_name(dest.name + ".part")
    last_error = None
    for endpoint in _ENDPOINTS:
        for _ in range(3):
            if should_stop():
                return False
            pos = part.stat().st_size if part.exists() else 0
            headers = {"Range": f"bytes={pos}-"} if pos else {}
            try:
                url = endpoint + f"/{repo_id}/resolve/main/{filename}"
                with requests.get(url, headers=headers, stream=True, timeout=(15, 90)) as response:
                    if response.status_code == 416 and part.exists():
                        os.replace(part, dest)
                        return True
                    response.raise_for_status()

                    total = None
                    if response.status_code == 206:
                        content_range = response.headers.get("Content-Range", "")
                        if "/" in content_range:
                            total = int(content_range.rsplit("/", 1)[1])
                    else:
                        # The server ignored our Range header; restart this file.
                        pos = 0
                        length = response.headers.get("Content-Length")
                        total = int(length) if length else None

                    done = pos
                    last_report = 0.0
                    with open(part, "wb" if pos == 0 else "ab") as handle:
                        for chunk in response.iter_content(1024 * 256):
                            if should_stop():
                                return False
                            if not chunk:
                                continue
                            handle.write(chunk)
                            done += len(chunk)
                            now = time.time()
                            if progress and (now - last_report > 0.4 or (total and done >= total)):
                                last_report = now
                                if total:
                                    progress(f"正在下载 {filename}（{done * 100 // total}%）")
                                else:
                                    progress(f"正在下载 {filename}（{_format_size(done)}）")

                    size = part.stat().st_size if part.exists() else 0
                    if size == 0:
                        last_error = "服务器返回了空文件"
                    elif total is not None and size < total:
                        last_error = f"连接中断（{_format_size(size)} / {_format_size(total)}）"
                    else:
                        os.replace(part, dest)
                        return True
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            if should_stop():
                return False
            time.sleep(1)
    raise RuntimeError(f"下载 {filename} 失败：{last_error}")


def ensure_model(repo_id, progress=None, should_stop=None):
    """Return a local model directory, downloading it (with resume) if needed."""
    existing = resolve_model_path(repo_id)
    if existing is not None:
        return existing

    should_stop = should_stop or (lambda: False)
    target = model_dir(repo_id)
    target.mkdir(parents=True, exist_ok=True)
    for filename in _MODEL_FILES:
        dest = target / filename
        if dest.is_file():
            if filename == "model.safetensors" and dest.stat().st_size > 1_000_000:
                continue
            if filename != "model.safetensors" and dest.stat().st_size > 10:
                continue
        if progress:
            progress(f"准备下载模型：{filename}")
        if not _fetch_file(repo_id, filename, dest, progress, should_stop):
            return None

    if not _is_complete(target):
        raise RuntimeError("模型文件下载不完整，请重试。")
    return target


class DepthWorker(QThread):
    finished_depth = Signal(object)
    failed = Signal(str)
    progress = Signal(str)

    # Keep only the active pipeline alive; large models would otherwise stack up.
    _pipeline = None
    _pipeline_key = None

    def __init__(self, image, generation=0, model_id=DEFAULT_MODEL):
        super().__init__()
        self.image = image
        self.generation = generation
        self.model_id = model_id

    @classmethod
    def _get_pipeline(cls, model_path):
        import torch
        from transformers import pipeline

        key = str(model_path)
        if cls._pipeline_key != key:
            device = 0 if torch.cuda.is_available() else -1
            cls._pipeline = pipeline(
                task="depth-estimation",
                model=key,
                device=device,
            )
            cls._pipeline_key = key
        return cls._pipeline

    def run(self):
        try:
            if self.isInterruptionRequested():
                return

            import cv2
            import numpy as np

            model_path = ensure_model(
                self.model_id,
                progress=self.progress.emit,
                should_stop=self.isInterruptionRequested,
            )
            if model_path is None or self.isInterruptionRequested():
                return

            estimator = self._get_pipeline(model_path)
            if self.isInterruptionRequested():
                return

            pil = self._to_pil(self.image)
            result = estimator(pil)
            if self.isInterruptionRequested():
                return

            depth_img = result["depth"]
            depth = np.asarray(depth_img).astype(np.float32)
            if depth.ndim == 3:
                depth = cv2.cvtColor(depth, cv2.COLOR_RGB2GRAY).astype(np.float32)
            depth = cv2.resize(depth, (self.image.shape[1], self.image.shape[0]), interpolation=cv2.INTER_CUBIC)
            lo, hi = float(depth.min()), float(depth.max())
            if hi > lo:
                depth = (depth - lo) / (hi - lo)
            self.finished_depth.emit(depth.astype(np.float32))
        except Exception as exc:
            if self.isInterruptionRequested():
                return
            if getattr(sys, "frozen", False):
                # 打包成 exe 时没有 torch/transformers，"装依赖"这句话对用户没有意义
                detail = (
                    f"无法运行 AI 深度估算（模型：{model_label(self.model_id)}）。\n\n"
                    "当前是轻量版可执行文件，没有内置 AI 依赖。\n"
                    "请改用「导入深度图」，或继续使用“中心对焦 / 手动焦点”。\n\n"
                    f"详细信息：\n{exc}"
                )
            else:
                detail = (
                    f"无法运行 AI 深度估算（模型：{model_label(self.model_id)}）。\n\n"
                    "请确认已安装 requirements-ai.txt 中的依赖，并且首次使用时可联网下载模型。\n"
                    "也可以换用已内置的 Small 模型，或继续使用“中心对焦/手动焦点”。\n\n"
                    "详细信息：\n" + str(exc)
                )
            self.failed.emit(detail)

    @staticmethod
    def _to_pil(rgb):
        from PIL import Image
        return Image.fromarray(rgb)
