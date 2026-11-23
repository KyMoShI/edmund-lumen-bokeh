"""仅移除已确认完全相同的顶层动态库副本。"""
from pathlib import Path
import hashlib


def slim_payload(payload: Path) -> dict:
    internal = payload / "_internal"
    removed = []
    for candidate in sorted(internal.glob("*.dll")):
        for folder in ("torch/lib", "numpy.libs", "pandas.libs", "PySide6", "shiboken6"):
            canonical = internal / folder / candidate.name
            if not canonical.is_file() or canonical.stat().st_size != candidate.stat().st_size:
                continue
            with candidate.open("rb") as first, canonical.open("rb") as second:
                if hashlib.file_digest(first, "sha256").digest() != hashlib.file_digest(second, "sha256").digest():
                    continue
            removed.append({"file": candidate.relative_to(payload).as_posix(), "bytes": candidate.stat().st_size,
                            "retained": canonical.relative_to(payload).as_posix()})
            candidate.unlink()
            break
    for candidate in sorted((internal / "cv2").glob("opencv_videoio_ffmpeg*.dll")):
        removed.append({"file": candidate.relative_to(payload).as_posix(), "bytes": candidate.stat().st_size,
                        "reason": "程序仅处理静态图像，不使用视频解码后端"})
        candidate.unlink()
    return {"removed": removed, "saved_bytes": sum(item["bytes"] for item in removed)}
