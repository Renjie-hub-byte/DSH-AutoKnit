"""fw-runner IO 工具：fs 原子写（dsh fs 原子写本地形态，同 fw-scaffold/io_utils 同款）。

原子写 = 同目录临时文件 + flush/fsync + os.replace（rename 原子替换）。
用途：REVIEW.md 状态键、总日志 dispatch.jsonl、快照.json —— 多 worker 并发写共享文件时
rename 原子性即并发防护（不用 Redis/外部锁，符合技术约束）。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

ENCODING = "utf-8"


def atomic_write_text(path: str | Path, content: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".tmp-", suffix=".fwrunner")
    try:
        with os.fdopen(fd, "w", encoding=ENCODING, newline="\n") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_append_jsonl(path: str | Path, obj: dict) -> None:
    """单行 JSON 追加（dispatch.jsonl）。小写入 + O_APPEND 原子性足够；
    更严格场景可用 atomic_write_text 全量重写（本 runner 事件均单写者串行 emit）。"""
    import json
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, ensure_ascii=False) + "\n"
    with open(p, "a", encoding=ENCODING) as f:
        f.write(line)
        f.flush()
