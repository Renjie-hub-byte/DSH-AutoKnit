"""fw-scaffold IO 工具：fs 原子写 + expected 版本防护（manifest 指纹守卫）。

- atomic_write_text(path, content)：同目录临时文件 + flush/fsync + os.replace
  原子替换（等价 dsh fs 原子写能力；不用 Redis/外部锁，同目录 rename 即并发防护）。
- expected 版本防护：生成完成后在任务根写 .scaffold-manifest.json（记录
  scaffold 版本 + task.yaml 指纹 + 每个生成文件的 sha256）。再次生成时先比对：
    - task.yaml 指纹变了              → 目录是另一个任务的 → 拒绝（ExpectedVersionMismatch）
    - 某个生成文件被外部修改（hash 变） → 拒绝覆盖用户改动（ExpectedVersionMismatch）
    - 目录非空但无 manifest            → 不是本脚手架建的 → 拒绝
  以上三种均需 --force 才能越过（force 会重新生成并刷新 manifest）。
  完全一致时视为幂等重跑：允许（重写相同字节，无破坏）。
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Dict, Optional

SCAFFOLD_VERSION = "1.0.0"
SCHEMA_VERSION = 2  # 目录规范 v2
MANIFEST_NAME = ".scaffold-manifest.json"
VERSION_FILE_NAME = ".scaffold-version"

ENCODING = "utf-8"


class ExpectedVersionMismatch(Exception):
    """目录已存在且由不同版本/内容生成，拒绝覆盖。需 --force 或换输出目录。"""


def atomic_write_text(path: Path, content: str) -> None:
    """fs 原子写：同目录临时文件 → flush+fsync → os.replace（rename 原子替换）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".scaffold")
    try:
        with os.fdopen(fd, "w", encoding=ENCODING, newline="\n") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    with open(path, "rb") as f:
        return sha256_bytes(f.read())


# ---------------------------------------------------------------- manifest

def load_manifest(root: Path) -> Optional[Dict]:
    p = Path(root) / MANIFEST_NAME
    if not p.exists():
        return None
    try:
        with open(p, "r", encoding=ENCODING) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def dump_manifest(root: Path, task_fingerprint: str, files: Dict[str, str],
                  generated_at: str) -> None:
    manifest = {
        "scaffold_version": SCAFFOLD_VERSION,
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "task_file": "task.yaml",
        "task_fingerprint": task_fingerprint,
        "files": dict(sorted(files.items())),
    }
    atomic_write_text(Path(root) / MANIFEST_NAME,
                      json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    atomic_write_text(Path(root) / VERSION_FILE_NAME,
                      f"fw-scaffold/{SCAFFOLD_VERSION}\n目录规范v{SCHEMA_VERSION}\n")


def _non_hidden_entries(root: Path):
    return [e for e in root.iterdir() if not e.name.startswith(".")]


def guard_existing_dir(root: Path, task_yaml_content: str, force: bool) -> str:
    """expected 版本防护。返回 "fresh" | "idempotent" | "forced"；不满足则抛 ExpectedVersionMismatch。

    fresh      : 目录不存在或为空目录（无非隐藏内容）→ 可全新生成
    idempotent : 已有 manifest 且 task.yaml 指纹与全部生成文件 hash 一致 → 幂等重跑
    forced     : force=True 且已有内容（含不匹配）→ 允许覆盖并刷新 manifest
    """
    root = Path(root)
    if not root.exists():
        return "fresh"
    entries = _non_hidden_entries(root)
    manifest = load_manifest(root)

    if manifest is None:
        if force:
            return "forced"
        if entries:
            raise ExpectedVersionMismatch(
                f"目标目录已存在且非空（{root}），但无 {MANIFEST_NAME} 清单，"
                "无法确认来源，拒绝覆盖（expected 版本防护）。用 --force 覆盖或换 --output 目录。")
        return "fresh"

    # 有 manifest：校验 task.yaml 指纹 + 每个生成文件 hash
    new_fp = sha256_bytes(task_yaml_content.encode(ENCODING))
    if manifest.get("task_fingerprint") != new_fp:
        if force:
            return "forced"
        raise ExpectedVersionMismatch(
            f"目录 {root} 已由另一份 task.yaml 生成（指纹不一致），"
            "拒绝覆盖（expected 版本防护）。用 --force 覆盖或另建目录。")

    modified: list = []
    for rel, expected_hash in (manifest.get("files") or {}).items():
        p = root / rel
        if not p.exists():
            modified.append(f"{rel}（缺失）")
            continue
        try:
            if sha256_file(p) != expected_hash:
                modified.append(rel)
        except OSError as e:
            modified.append(f"{rel}（读取失败:{e}）")
    if modified:
        if force:
            return "forced"
        raise ExpectedVersionMismatch(
            "生成文件被外部修改，拒绝覆盖（expected 版本防护）: " + ", ".join(sorted(modified))
            + "。用 --force 覆盖这些文件。")
    return "idempotent"


def write_guard_manifest(root: Path, task_yaml_content: str, files: Dict[str, str],
                         generated_at: str) -> None:
    fp = sha256_bytes(task_yaml_content.encode(ENCODING))
    dump_manifest(root, fp, files, generated_at)
