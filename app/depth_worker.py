import gc
import json
import os
import sys
import time
from dataclasses import dataclass
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


@dataclass(frozen=True)
class ModelSpec:
    """一个可选的深度模型。

    backend 决定推理走哪条路：
      - ``transformers``：AutoModelForDepthEstimation（Depth Anything V2 系列）
      - ``da3``：ByteDance Depth Anything 3，自定义网络，需要单独装依赖
    files 是这个仓库真正存在的文件；DA3 没有 preprocessor_config.json。

    orientation 描述输出的数值方向，用于统一到"值大 = 近"：
      - ``disparity``：视差/逆深度，值大 = 近（DA2 系列）
      - ``depth``：真实深度，值大 = 远（DA3 系列，归一化前要取反）
    """

    name: str
    repo: str
    size_label: str
    backend: str = "transformers"
    files: tuple[str, ...] = ("config.json", "preprocessor_config.json", "model.safetensors")
    license: str = "Apache-2.0"
    preset: str = ""  # DA3 包内的配置名，如 da3-small
    orientation: str = "disparity"


MODEL_SPECS = (
    ModelSpec("Depth Anything V2 Small", "depth-anything/Depth-Anything-V2-Small-hf", "约 95 MB"),
    ModelSpec("Depth Anything V2 Base", "depth-anything/Depth-Anything-V2-Base-hf", "约 390 MB"),
    ModelSpec("Depth Anything V2 Large", "depth-anything/Depth-Anything-V2-Large-hf", "约 1.3 GB"),
    # 只放 Apache-2.0 的权重；DA3-LARGE/GIANT 是 CC BY-NC 4.0，不能随应用分发。
    ModelSpec(
        "DA3 Small",
        "depth-anything/DA3-SMALL",
        "约 131 MB",
        backend="da3",
        files=("config.json", "model.safetensors"),
        preset="da3-small",
        orientation="depth",
    ),
    ModelSpec(
        "DA3 Base",
        "depth-anything/DA3-BASE",
        "约 480 MB",
        backend="da3",
        files=("config.json", "model.safetensors"),
        preset="da3-base",
        orientation="depth",
    ),
)

# 兼容旧调用方；新代码直接用 MODEL_SPECS
MODEL_CHOICES = [(spec.name, spec.repo, spec.size_label) for spec in MODEL_SPECS]
DEFAULT_MODEL = MODEL_SPECS[0].repo
_SPEC_BY_REPO = {spec.repo: spec for spec in MODEL_SPECS}

# hf-mirror is usually reachable from mainland China; huggingface.co is the fallback.
_ENDPOINTS = ("https://hf-mirror.com", "https://huggingface.co")


def model_spec(repo_id: str) -> ModelSpec:
    """内置模型返回注册表条目；导入的模型沿用 transformers 路径。"""
    spec = _SPEC_BY_REPO.get((repo_id or "").strip())
    if spec is not None:
        return spec
    return ModelSpec(name=repo_id or "", repo=repo_id or "", size_label="")


def model_label(repo_id):
    for name, rid, _size in MODEL_CHOICES:
        if rid == repo_id:
            return name
    for item in _load_imported():
        if item["ref"] == repo_id:
            return item["name"]
    return repo_id


def _repo_slug(repo_id):
    return repo_id.replace("/", "--")


def model_dir(repo_id):
    return _CACHE_DIR / _repo_slug(repo_id)


def _is_complete(path):
    """A usable model folder needs a config plus at least one weight file."""
    config = path / "config.json"
    if not (config.is_file() and config.stat().st_size > 10):
        return False
    for weights in ("model.safetensors", "pytorch_model.bin"):
        candidate = path / weights
        if candidate.is_file() and candidate.stat().st_size > 1_000_000:
            return True
    return False


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
    """Return a local model directory, or None when it still needs downloading.

    ``repo_id`` is either a Hugging Face id (``owner/name``) or a path to a
    folder the user imported from disk; an imported folder is used in place.
    """
    ref = (repo_id or "").strip()
    if not ref:
        return None
    folder = Path(os.path.expanduser(ref))
    if folder.is_dir():
        return folder if _is_complete(folder) else None
    local = model_dir(ref)
    if _is_complete(local):
        return local
    return _legacy_snapshot(ref)


# --------------------------------------------------------------------------
# Imported models: a local folder or an extra Hugging Face repo. Persisted so
# the list survives the next launch.
# --------------------------------------------------------------------------

_IMPORTED_FILE = _CACHE_DIR / "imported_models.json"


def _load_imported():
    try:
        raw = json.loads(_IMPORTED_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        ref = str(item.get("ref", "")).strip()
        if ref:
            entries.append(
                {"name": str(item.get("name", "")).strip() or ref, "ref": ref}
            )
    return entries


def _save_imported(entries):
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _IMPORTED_FILE.write_text(
            json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        # A read-only install folder must not break model import.
        pass


def imported_models():
    """Custom models the user added, as ``[{"name": ..., "ref": ...}]``."""
    return _load_imported()


def add_imported_model(name, ref):
    """Add (or replace) an imported model and return the updated list."""
    ref = (ref or "").strip()
    if not ref:
        return _load_imported()
    entries = [item for item in _load_imported() if item["ref"] != ref]
    entries.append({"name": (name or "").strip() or ref, "ref": ref})
    _save_imported(entries)
    return entries


def normalize_repo(text):
    """Turn a Hugging Face id or page URL into ``owner/name``."""
    text = (text or "").strip().rstrip("/")
    if not text:
        return ""
    if "://" in text:
        text = text.split("://", 1)[1]
        text = text.split("/", 1)[1] if "/" in text else ""
    text = text.split("#", 1)[0].split("?", 1)[0].strip("/")
    parts = [part for part in text.split("/") if part]
    for marker in ("tree", "blob", "resolve", "raw", "commit"):
        if marker in parts:
            parts = parts[:parts.index(marker)]
    if len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return parts[0] if parts else ""


def is_complete_model_dir(path):
    """True when ``path`` is a usable local model folder."""
    return _is_complete(Path(os.path.expanduser(str(path))))


def is_local_ref(ref):
    """True when ``ref`` points at a folder on disk rather than a repo id."""
    return os.path.isdir(os.path.expanduser((ref or "").strip()))


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
    """Return a local model directory, downloading it (with resume) if needed.

    Accepts a Hugging Face id (downloaded into ``models/`` when missing) or a
    folder the user imported from disk (used in place, never downloaded).
    """
    ref = (repo_id or "").strip()
    folder = Path(os.path.expanduser(ref)) if ref else None
    if folder is not None and folder.is_dir():
        if is_complete_model_dir(folder):
            return folder
        raise RuntimeError(
            f"本地模型目录不完整：{folder}\n"
            "需要包含 config.json，以及权重文件 model.safetensors 或 pytorch_model.bin。"
        )

    existing = resolve_model_path(ref)
    if existing is not None:
        return existing

    should_stop = should_stop or (lambda: False)
    target = model_dir(ref)
    target.mkdir(parents=True, exist_ok=True)
    spec = model_spec(ref)
    for filename in spec.files:
        dest = target / filename
        if dest.is_file():
            if filename == "model.safetensors" and dest.stat().st_size > 1_000_000:
                continue
            if filename != "model.safetensors" and dest.stat().st_size > 10:
                continue
        if progress:
            progress(f"准备下载模型：{filename}")
        if not _fetch_file(ref, filename, dest, progress, should_stop):
            return None

    if not _is_complete(target):
        raise RuntimeError("模型文件下载不完整，请重试。")
    return target


# --------------------------------------------------------------------------
# 推理后端
# --------------------------------------------------------------------------


class _TransformersBackend:
    """Depth Anything V2 系列：transformers 的 AutoModelForDepthEstimation。"""

    def __init__(self, model_path):
        import torch
        from transformers import AutoImageProcessor, AutoModelForDepthEstimation

        self._torch = torch
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.processor = AutoImageProcessor.from_pretrained(model_path)
        self.model = AutoModelForDepthEstimation.from_pretrained(model_path).to(device).eval()

    def infer(self, pil, size, notify, cancelled):
        import torch

        base = {"images": pil, "return_tensors": "pt"}
        if size > 0:
            # 14 是模型的 patch size，分辨率要取它的整数倍
            size = max(14, int(round(size / 14.0)) * 14)
            try:
                inputs = self.processor(**base, size={"height": size, "width": size})
            except Exception:
                if cancelled():
                    raise
                notify("该模型不支持自定义分辨率，回退到默认分辨率。")
                inputs = self.processor(**base)
        else:
            inputs = self.processor(**base)

        with torch.no_grad():
            outputs = self.model(**inputs)
        return (
            torch.nn.functional.interpolate(
                outputs.predicted_depth.unsqueeze(1),
                size=pil.size[::-1],
                mode="bicubic",
                align_corners=False,
            )
            .squeeze()
            .cpu()
            .numpy()
        )


class _Da3Backend:
    """Depth Anything 3 的薄适配层。

    故意不走 ``depth_anything_3.api``：那个模块在 import 时会连带加载导出功能，
    进而要求 moviepy 1.x / pycolmap / gsplat，Windows 上装不动也用不上。
    这里只取它的零件：配置 → 网络 → 权重 → 输入/输出处理器。
    """

    def __init__(self, model_path, preset):
        try:
            import torch
            from safetensors.torch import load_file
            from depth_anything_3.cfg import create_object, load_config
            from depth_anything_3.registry import MODEL_REGISTRY
            from depth_anything_3.utils.io.input_processor import InputProcessor
            from depth_anything_3.utils.io.output_processor import OutputProcessor
        except ImportError as exc:
            missing = getattr(exc, "name", None) or "未知模块"
            raise RuntimeError(
                f"DA3 依赖不完整：缺少 {missing}。\n\n"
                "补装辅助依赖（最常见的情况）：\n"
                "  .venv\\Scripts\\python.exe -m pip install -r requirements-da3.txt\n"
                "如果 depth-anything-3 本身也没装：\n"
                "  .venv\\Scripts\\python.exe -m pip install --no-deps depth-anything-3==0.1.1\n"
                "（必须带 --no-deps：否则会把 numpy 钉回 <2，还会拖入 moviepy/pycolmap\n"
                "这类只有 3D 导出才需要的重依赖。）\n\n"
                f"原始错误：{exc}"
            ) from exc

        if preset not in MODEL_REGISTRY:
            raise RuntimeError(f"DA3 包内没有预设 {preset!r}，模型与代码版本可能不匹配。")

        weights = Path(model_path) / "model.safetensors"
        if not weights.is_file():
            raise RuntimeError(f"缺少权重文件：{weights}")

        self._torch = torch
        net = create_object(load_config(MODEL_REGISTRY[preset]))
        state = load_file(str(weights))
        # HF 检查点是从 DepthAnything3 包装类存的，键名多一层 "model."
        state = {
            key[len("model.") :] if key.startswith("model.") else key: value
            for key, value in state.items()
        }
        missing, unexpected = net.load_state_dict(state, strict=False)
        if unexpected:
            raise RuntimeError(f"权重与网络结构不匹配：多余键 {len(unexpected)} 个")
        # 少量 missing 来自 pose/ray 的 aux 头，不影响 depth
        self.missing_keys = missing

        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.net = net.to(self.device).eval()
        self.input_processor = InputProcessor()
        self.output_processor = OutputProcessor()

    def infer(self, pil, size, notify, cancelled):
        import torch

        # 单张图必须 sequential=True：默认会开 8 线程池，Windows 上要建命名管道
        process_res = int(size) if size and size > 0 else 504
        imgs, _, _ = self.input_processor(
            [pil], None, None, process_res, "upper_bound_resize", sequential=True
        )
        if cancelled():
            raise RuntimeError("已取消")
        with torch.inference_mode():
            raw = self.net(
                imgs[None].float().to(self.device),
                None,
                None,
                [],  # export_feat_layers 必须是 list，不能是 None
                False,
                False,
                "saddle_balanced",
            )
        return self.output_processor(raw).depth[0]


# --------------------------------------------------------------------------
# 工作线程
# --------------------------------------------------------------------------


class DepthWorker(QThread):
    finished_depth = Signal(object)
    failed = Signal(str)
    progress = Signal(str)

    # 同一时刻只保留一个后端；大模型来回切换会堆积显存
    _backend = None
    _backend_key = None

    def __init__(self, image, generation=0, model_id=DEFAULT_MODEL, depth_size=0):
        super().__init__()
        self.image = image
        self.generation = generation
        self.model_id = model_id
        self.depth_size = int(depth_size or 0)

    @classmethod
    def _get_backend(cls, spec, model_path):
        key = (spec.backend, spec.preset, str(model_path))
        if cls._backend_key != key:
            cls.release_backend()
            if spec.backend == "da3":
                cls._backend = _Da3Backend(model_path, spec.preset)
            else:
                cls._backend = _TransformersBackend(model_path)
            cls._backend_key = key
        return cls._backend

    @classmethod
    def release_backend(cls):
        """丢掉缓存的后端并尽量归还显存。"""
        cls._backend = None
        cls._backend_key = None
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    @staticmethod
    def _normalise(depth, spec):
        """归一化到 0..1。

        DA3 的相对深度动态范围很窄（实测 span ≈ 0.09），直接 min-max 会把噪声
        放大，所以按 2%–98% 分位数拉伸；DA2 维持原来的 min-max，行为不变。
        """
        import numpy as np

        if spec.backend == "da3":
            low = float(np.percentile(depth, 2.0))
            high = float(np.percentile(depth, 98.0))
            if high - low < 1e-6:
                low, high = float(depth.min()), float(depth.max())
            if high - low < 1e-6:
                return np.zeros_like(depth)
            return np.clip((depth - low) / (high - low), 0.0, 1.0).astype(np.float32)

        low, high = float(depth.min()), float(depth.max())
        if high > low:
            return ((depth - low) / (high - low)).astype(np.float32)
        return np.zeros_like(depth)

    def run(self):
        try:
            if self.isInterruptionRequested():
                return

            import cv2
            import numpy as np

            spec = model_spec(self.model_id)
            model_path = ensure_model(
                self.model_id,
                progress=self.progress.emit,
                should_stop=self.isInterruptionRequested,
            )
            if model_path is None or self.isInterruptionRequested():
                return

            backend = self._get_backend(spec, model_path)
            if self.isInterruptionRequested():
                return

            pil = self._to_pil(self.image)
            depth = backend.infer(
                pil, self.depth_size, self.progress.emit, self.isInterruptionRequested
            )
            if self.isInterruptionRequested():
                return

            depth = np.asarray(depth).astype(np.float32)
            if depth.ndim == 3:
                depth = cv2.cvtColor(depth, cv2.COLOR_RGB2GRAY).astype(np.float32)
            depth = cv2.resize(
                depth,
                (self.image.shape[1], self.image.shape[0]),
                interpolation=cv2.INTER_CUBIC,
            )
            if spec.orientation == "depth":
                # DA3 直接预测深度（值大 = 远），DA2 输出视差（值大 = 近）。
                # 统一成"值大 = 近"：否则预览黑白相反，同一个焦点滑块在两种模型下
                # 表示的远近也相反，切换模型时虚化的前后关系会整个翻转。
                depth = -depth
            depth = self._normalise(depth, spec)
            self.finished_depth.emit(depth.astype(np.float32))
        except Exception as exc:
            if self.isInterruptionRequested():
                return
            if getattr(sys, "frozen", False):
                hint = (
                    "当前是轻量版可执行文件，没有内置 AI 依赖。\n"
                    "请改用「导入深度图」，或继续使用“中心对焦 / 手动焦点”。\n\n"
                )
            else:
                hint = (
                    "请确认已安装 requirements-ai.txt 中的依赖，并且首次使用时可联网下载模型。\n"
                    "DA3 模型另需按 README 的「Depth Anything 3」一节安装。\n"
                    "也可以换用 Depth Anything V2，或继续使用“中心对焦/手动焦点”。\n\n"
                )
            self.failed.emit(
                f"无法运行 AI 深度估算（模型：{model_label(self.model_id)}）。\n\n"
                + hint
                + "详细信息：\n"
                + str(exc)
            )

    @staticmethod
    def _to_pil(rgb):
        from PIL import Image

        return Image.fromarray(rgb)
