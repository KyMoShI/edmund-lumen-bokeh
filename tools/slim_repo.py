"""仓库瘦身：把 docs/images 里的大图压小，重建 4 个里程碑提交。

只改图片二进制与 README 里的图片链接，源码/文档正文不动。
压缩规则：
  - 照片类 → JPEG q85、长边 1600px（文件名改成 .jpg，README 链接同步替换）
  - 截图类（颜色数少）→ 保留 PNG、长边 1280px，optimize
  - 原本就是 .jpeg/.jpg 的 → 原地重压，文件名不变

用法：
    .venv\\Scripts\\python.exe tools\\slim_repo.py --dry-run
    .venv\\Scripts\\python.exe tools\\slim_repo.py
"""

from __future__ import annotations

import argparse
import io
import os
import subprocess
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT / "github-upload"

NAME = "Moki_Makesi"
EMAIL = "48328991+KyMoShI@users.noreply.github.com"

MIN_BYTES = 900 * 1024          # 小于这个的图片不动
PHOTO_MAX = 1600                # 照片长边
SHOT_MAX = 1280                 # 截图长边
JPEG_QUALITY = 85


def git(*args: str, input_bytes: bytes | None = None, env: dict | None = None) -> bytes:
    return subprocess.run(
        ["git", "-C", str(REPO), *args],
        input=input_bytes,
        capture_output=True,
        check=True,
        env=env,
    ).stdout


def list_entries(tree: str):
    raw = git("ls-tree", "-z", tree)
    for chunk in raw.split(b"\x00"):
        if not chunk:
            continue
        meta, _, path = chunk.partition(b"\t")
        mode, kind, sha = meta.decode().split()
        yield mode, kind, sha, path.decode("utf-8", "surrogateescape")


def list_blobs(tree: str):
    """递归列出所有 blob（带完整路径）。"""
    raw = git("ls-tree", "-r", "-z", tree)
    for chunk in raw.split(b"\x00"):
        if not chunk:
            continue
        meta, _, path = chunk.partition(b"\t")
        mode, kind, sha = meta.decode().split()
        if kind == "blob":
            yield sha, path.decode("utf-8", "surrogateescape")


def compress(data: bytes, path: str) -> tuple[bytes, str]:
    """返回 (新字节, 新文件名)。"""
    image = Image.open(io.BytesIO(data))
    image.load()

    suffix = Path(path).suffix.lower()
    # 截图判定：PNG 且颜色数不多
    is_shot = False
    if suffix == ".png":
        probe = image.convert("RGB")
        probe.thumbnail((512, 512))
        is_shot = probe.getcolors(maxcolors=20000) is not None

    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGBA")
        canvas = Image.new("RGB", image.size, (255, 255, 255))
        canvas.paste(image, mask=image.split()[-1])
        image = canvas
    else:
        image = image.convert("RGB")

    if is_shot:
        image.thumbnail((SHOT_MAX, SHOT_MAX), Image.LANCZOS)
        out = io.BytesIO()
        image.save(out, format="PNG", optimize=True)
        return out.getvalue(), Path(path).name

    image.thumbnail((PHOTO_MAX, PHOTO_MAX), Image.LANCZOS)
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
    name = Path(path).with_suffix(".jpg").name
    return out.getvalue(), name


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    commits = git("rev-list", "--reverse", "main").decode().split()
    if not commits:
        print("main 上没有提交")
        return 1
    head_tree = git("rev-parse", "HEAD^{tree}").strip().decode()

    # 1) 找出 HEAD 里的大图及其新内容
    blobs: dict[str, bytes] = {}
    renames: dict[str, str] = {}
    old_paths: list[str] = []
    for sha, path in list_blobs(head_tree):
        if not path.startswith("docs/images/"):
            continue
        size = int(git("cat-file", "-s", sha).strip())
        if size < MIN_BYTES:
            continue
        old_paths.append(path)
        data = compress(git("cat-file", "blob", sha), path)
        blobs[path] = data[0]
        new_name = data[1]
        if new_name != Path(path).name:
            renames[path] = str(Path(path).with_name(new_name))

    if not old_paths:
        print("没有需要压缩的图片")
        return 0

    total_old = 0
    for sha, path in list_blobs(head_tree):
        if path in old_paths:
            total_old += int(git("cat-file", "-s", sha).strip())
    total_new = sum(len(v) for v in blobs.values())
    print(f"待压缩 {len(old_paths)} 张图：{total_old / 1048576:.1f} MB -> {total_new / 1048576:.1f} MB")
    for p in old_paths:
        new = renames.get(p, p)
        print(f"  {Path(p).name:28s} -> {Path(new).name:28s} {len(blobs[p])/1024:7.0f} KB")

    if args.dry_run:
        return 0

    # 2) 写入新 blob
    new_sha: dict[str, str] = {}
    for path, data in blobs.items():
        new_sha[path] = git("hash-object", "-w", "--stdin", input_bytes=data).strip().decode()

    # 3) 递归重写 tree
    def rewrite(tree: str, prefix: str = "") -> str:
        entries = []
        for mode, kind, sha, name in list_entries(tree):
            full = prefix + name
            if kind == "tree":
                sha = rewrite(sha, full + "/")
            elif full in new_sha:
                sha = new_sha[full]
                name = Path(renames.get(full, full)).name
            elif name.endswith((".md", ".txt")):
                text = git("cat-file", "blob", sha).decode("utf-8")
                patched = text
                for old, new in renames.items():
                    patched = patched.replace(Path(old).name, Path(new).name)
                patched = patched.replace("原图1.jepg", "原图1.jpg")
                if patched != text:
                    sha = git("hash-object", "-w", "--stdin",
                              input_bytes=patched.encode("utf-8")).strip().decode()
            entries.append(f"{mode} {kind} {sha}\t{name}".encode() + b"\x00")
        return git("mktree", "-z", input_bytes=b"".join(entries)).strip().decode()

    # 4) 重建提交（沿用原标题/作者时间）
    parent = None
    created = []
    for old in commits:
        meta = git("show", "-s", "--format=%s%n%aI%n%b", old).decode("utf-8")
        lines = meta.split("\n")
        subject, date = lines[0], lines[1]
        body = "\n".join(lines[2:]).strip()
        message = subject + ("\n\n" + body if body else "") + "\n"
        tree = rewrite(git("rev-parse", f"{old}^{{tree}}").strip().decode())
        env = dict(os.environ)
        env.update(
            GIT_AUTHOR_NAME=NAME, GIT_AUTHOR_EMAIL=EMAIL, GIT_AUTHOR_DATE=date,
            GIT_COMMITTER_NAME=NAME, GIT_COMMITTER_EMAIL=EMAIL, GIT_COMMITTER_DATE=date,
        )
        cmd = ["commit-tree", tree] + (["-p", parent] if parent else [])
        parent = git(*cmd, input_bytes=message.encode("utf-8"), env=env).strip().decode()
        created.append(parent)

    git("update-ref", "refs/backup/pre-slim/main", git("rev-parse", "main").strip().decode())
    git("update-ref", "refs/heads/main", created[-1])
    git("update-ref", "refs/heads/release/v1.1.0", created[1])
    git("update-ref", "refs/tags/v1.1.0", created[1])
    git("update-ref", "refs/tags/v1.1.1", created[2])

    print(f"\n完成：main -> {created[-1][:7]}")
    print(f"HEAD tree: {git('rev-parse', 'HEAD^{tree}').strip().decode()[:7]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
