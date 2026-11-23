"""Portable, atomic persistence for the welcome page's last editing session.

The archive contains JSON controls and optional float32 depth maps. Original
images remain on disk and are reopened by the caller; history stays in memory.
No Qt, model loading, pickle, or executable serialization is involved.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping
import zipfile

import numpy as np


SESSION_FILENAME = "last-session.npz"
SCHEMA_VERSION = 1
_MAX_METADATA_BYTES = 1_000_000
_MAX_DEPTH_PIXELS = 100_000_000
_MAX_ARCHIVE_BYTES = 1_000_000_000
_ARRAY_KEYS = ("depth", "depth_full")
_IMAGE_KEYS = ("original", "original_full")
_TEXT_KEYS = ("image_path", "depth_name", "model_ref")
_SOURCE_SIGNATURE_KEY = "image_source_signature"


@dataclass(frozen=True)
class SessionLoadResult:
    """A recoverable state, plus a nonfatal explanation if recovery degraded.

    ``state`` has no image pixels. When ``image_path`` remains present, the
    caller should decode it and set ``original_full`` before ``_restore``.
    A missing source still permits parameter recovery, with image/depth cleared.
    """

    state: dict[str, Any] | None = None
    warning: str = ""
    status: str = "missing"

    @property
    def available(self) -> bool:
        return self.state is not None


def _control_state(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the snapshot's scalar controls without serializing live objects."""
    result: dict[str, Any] = {}
    for key, value in snapshot.items():
        if key in (*_ARRAY_KEYS, *_IMAGE_KEYS, _SOURCE_SIGNATURE_KEY):
            continue
        if not isinstance(key, str) or len(key) > 128:
            raise ValueError("Invalid session control key")
        if isinstance(value, np.generic):
            value = value.item()
        if key in _TEXT_KEYS:
            if value is not None and not isinstance(value, (str, os.PathLike)):
                raise ValueError(f"Invalid session text: {key}")
            value = os.fspath(value) if value is not None else None
            if value is not None and (not isinstance(value, str) or len(value) > 32768):
                raise ValueError(f"Invalid session text length: {key}")
        elif key == "parked_bokeh_values":
            if not isinstance(value, Mapping):
                raise ValueError("Invalid parked session parameters")
            value = _control_state(value)
            if any(not isinstance(item, (int, bool)) for item in value.values()):
                raise ValueError("Invalid parked session parameter value")
        elif value is not None:
            # MainWindow's sliders and combo indices are integers. Refuse text
            # and fractional values that could crash Qt's setValue/index APIs.
            if isinstance(value, float):
                if not math.isfinite(value) or not value.is_integer():
                    raise ValueError(f"Invalid session control number: {key}")
                value = int(value)
            if not isinstance(value, (int, bool)) or abs(value) > 2**31 - 1:
                raise ValueError(f"Invalid session control value: {key}")
        result[key] = value
    return result


def _depth_array(value: Any) -> np.ndarray:
    array = np.asarray(value)
    if (array.ndim != 2 or not all(0 < n <= _MAX_DEPTH_PIXELS for n in array.shape)
            or array.size > _MAX_DEPTH_PIXELS or array.dtype.kind not in "fiu"):
        raise ValueError("Invalid session depth shape or type")
    array = np.ascontiguousarray(array, dtype=np.float32)
    if not np.isfinite(array).all():
        raise ValueError("Session depth contains nonfinite values")
    return array


def source_signature(path: str | os.PathLike[str] | None) -> dict[str, int] | None:
    """Capture the source identity alongside image decoding, not at session save.

    Callers may compare signatures captured before and after decoding. If they
    differ, retain ``None`` to invalidate any depth derived from uncertain pixels.
    """
    if not path:
        return None
    try:
        source = Path(path)
        if not source.is_file():
            return None
        stat = source.stat()
        return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    except (OSError, ValueError, TypeError):
        return None


def _validated_signature(value: Any) -> dict[str, int] | None:
    if value is None:
        return None
    if (not isinstance(value, Mapping) or set(value) != {"size", "mtime_ns"}
            or any(type(item) is not int for item in value.values())
            or not 0 <= value["size"] <= 2**63 - 1
            or not -(2**63) <= value["mtime_ns"] <= 2**63 - 1):
        raise ValueError("Invalid session source signature")
    return {"size": value["size"], "mtime_ns": value["mtime_ns"]}


def save_session(base_dir: str | os.PathLike[str], snapshot: Mapping[str, Any]) -> None:
    """Save controls and depths atomically; failures leave the prior file intact.

    Call only when the editing session should replace the saved one. Showing or
    closing the welcome page, or starting an untouched empty workspace, should
    not by itself call this function.

    Supply ``image_source_signature`` captured when the pixels were loaded. An
    explicit ``None`` marks an uncertain source identity and invalidates cached
    depths on next load. Omitting the key retains legacy current-stat behavior.
    """
    state = _control_state(snapshot)
    path = state.get("image_path")
    if path:
        state["image_path"] = path = str(Path(path).resolve())
    arrays = {key: _depth_array(snapshot[key]) for key in _ARRAY_KEYS
              if snapshot.get(key) is not None}
    image = snapshot.get("original_full")
    image_shape = list(image.shape[:2]) if isinstance(image, np.ndarray) and image.ndim >= 2 else None
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "state": state,
        "source": (_validated_signature(snapshot[_SOURCE_SIGNATURE_KEY])
                   if _SOURCE_SIGNATURE_KEY in snapshot else source_signature(path)),
        "image_shape": image_shape,
        "depth_keys": sorted(arrays),
    }
    encoded = json.dumps(metadata, ensure_ascii=False, allow_nan=False,
                         separators=(",", ":")).encode("utf-8")
    if len(encoded) > _MAX_METADATA_BYTES:
        raise ValueError("Session metadata is too large")
    arrays["metadata"] = np.frombuffer(encoded, dtype=np.uint8)
    directory = Path(base_dir)
    directory.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix="last-session.", suffix=".tmp",
                                         dir=directory, delete=False) as stream:
            temporary = Path(stream.name)
            np.savez_compressed(stream, **arrays)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, directory / SESSION_FILENAME)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _validate_members(stream) -> set[str]:
    """Check NPY headers before NumPy allocates arrays from a local archive."""
    allowed = {"metadata.npy", *(key + ".npy" for key in _ARRAY_KEYS)}
    with zipfile.ZipFile(stream) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or "metadata.npy" not in names or not set(names) <= allowed:
            raise ValueError("Invalid session archive members")
        for member in archive.infolist():
            with archive.open(member) as data:
                version = np.lib.format.read_magic(data)
                if version == (1, 0):
                    shape, _fortran, dtype = np.lib.format.read_array_header_1_0(data)
                elif version == (2, 0):
                    shape, _fortran, dtype = np.lib.format.read_array_header_2_0(data)
                else:
                    raise ValueError("Unsupported session array format")
                if dtype.hasobject:
                    raise ValueError("Object arrays are not session data")
                if member.filename == "metadata.npy":
                    if len(shape) != 1 or not 0 < shape[0] <= _MAX_METADATA_BYTES or dtype != np.dtype("uint8"):
                        raise ValueError("Invalid session metadata array")
                elif (len(shape) != 2 or not all(0 < n <= _MAX_DEPTH_PIXELS for n in shape)
                      or math.prod(shape) > _MAX_DEPTH_PIXELS or dtype != np.dtype("float32")):
                    raise ValueError("Invalid stored session depth")
                expected_bytes = math.prod(shape) * dtype.itemsize
                if data.tell() + expected_bytes != member.file_size:
                    raise ValueError("Session array length does not match its header")
    return {Path(name).stem for name in names} - {"metadata"}


def load_session(base_dir: str | os.PathLike[str]) -> SessionLoadResult:
    """Read one validated session; report unavailable/corrupt data without raising."""
    path = Path(base_dir) / SESSION_FILENAME
    try:
        if not path.is_file():
            return SessionLoadResult()
        if not 0 < path.stat().st_size <= _MAX_ARCHIVE_BYTES:
            raise ValueError("Invalid session archive size")
        with path.open("rb") as stream:
            depth_keys = _validate_members(stream)
            stream.seek(0)
            with np.load(stream, allow_pickle=False) as archive:
                metadata = json.loads(archive["metadata"].tobytes().decode("utf-8"))
                if not isinstance(metadata, dict):
                    raise ValueError("Invalid session metadata")
                if (type(metadata.get("schema_version")) is not int
                        or metadata["schema_version"] != SCHEMA_VERSION):
                    return SessionLoadResult(warning="上一会话的数据版本不受支持，请重新导入图片。",
                                             status="unsupported")
                if (not isinstance(metadata.get("state"), dict)
                        or metadata.get("depth_keys") != sorted(depth_keys)):
                    raise ValueError("Session metadata does not match its depth maps")
                state = _control_state(metadata["state"])
                stored_signature = _validated_signature(metadata.get("source"))
                for key in _ARRAY_KEYS:
                    state[key] = _depth_array(archive[key]) if key in depth_keys else None
        state["original"] = state["original_full"] = None
        state[_SOURCE_SIGNATURE_KEY] = stored_signature
        source_path = state.get("image_path")
        info = source_signature(source_path)
        if source_path and info is None:
            state["image_path"] = None
            state[_SOURCE_SIGNATURE_KEY] = None
            state["depth"] = state["depth_full"] = state["depth_name"] = None
            return SessionLoadResult(state, "上一会话的原图不可用，已恢复参数，请重新导入图片。", "source-missing")
        if source_path and info != stored_signature:
            state["depth"] = state["depth_full"] = state["depth_name"] = None
            return SessionLoadResult(state, "上一会话的原图已更新，已恢复参数；旧深度图未载入。", "source-changed")
        if not source_path:
            state[_SOURCE_SIGNATURE_KEY] = None
            state["depth"] = state["depth_full"] = state["depth_name"] = None
        return SessionLoadResult(state, status="ready")
    except (OSError, ValueError, TypeError, KeyError, EOFError, RuntimeError,
            OverflowError, RecursionError, MemoryError, zipfile.BadZipFile):
        return SessionLoadResult(warning="上一会话数据无法读取，请重新开始或导入图片。", status="invalid")
