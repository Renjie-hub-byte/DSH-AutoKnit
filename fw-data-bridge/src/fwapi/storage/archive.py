"""fwapi.storage.archive —— 归档状态存取（跨模块共享存储）。

契约（contract.yaml shared_data.stores[archive_store]）：
- 默认路径：<task_dir>/总日志/archived.json
- 可用环境变量覆盖绝对路径：AUTOKNIT_ARCHIVE_FILE
- 文件格式：{"archived": ["<run_id>", ...]}（archived: array of run_id (str)）

行为约定：
- 文件缺失/损坏/无 active 归档时 list 确定性返回 []；
- archive() 幂等：已归档的 run_id 重复归档不重复、不报错；
- 仅写归档文件，绝不写任务状态文件。
"""
from __future__ import annotations

import json
import os
import threading
from typing import List

ENV_ARCHIVE_FILE = "AUTOKNIT_ARCHIVE_FILE"
ARCHIVE_FILE = os.path.join("总日志", "archived.json")
_KEY = "archived"

# 归档读写为跨线程共享操作，用进程内锁保证并发安全（幂等读改写）。
_LOCK = threading.Lock()


def resolve_archive_path(task_dir: str) -> str:
    """解析归档文件绝对路径：优先环境变量覆盖，否则 task_dir 下约定相对路径。"""
    env_path = os.environ.get(ENV_ARCHIVE_FILE)
    if env_path:
        return env_path
    return os.path.join(task_dir, ARCHIVE_FILE)


def _read_ids(task_dir: str) -> List[str]:
    """内部读：解析归档文件为有序 run_id 列表；缺失/损坏返回 []。"""
    path = resolve_archive_path(task_dir)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        return []
    ids = payload.get(_KEY, []) if isinstance(payload, dict) else payload
    if not isinstance(ids, list):
        return []
    seen: List[str] = []
    for run_id in ids:
        if isinstance(run_id, str) and run_id and run_id not in seen:
            seen.append(run_id)
    return seen


def list_archived(task_dir: str) -> List[str]:
    """fwapi.tasks.archived 数据源：返回已归档 run_id 集合（保持写入顺序）。"""
    with _LOCK:
        return _read_ids(task_dir)


def is_archived(task_dir: str, run_id: str) -> bool:
    """判断某 run_id 是否已归档（幂等读，不触发写）。"""
    return run_id in _read_ids(task_dir)


def archive_run(task_dir: str, run_id: str) -> dict:
    """把 run_id 写入归档（幂等）。

    返回契约 fwapi.tasks.archive 响应：
      {ok: bool, run_id: str, archived: bool}
    - ok：本次写入是否成功（目录不可写/IO 失败为 False）；
    - archived：写入后该 run_id 是否处于已归档状态（成功即 True，重复归档仍 True）。
    """
    if not isinstance(run_id, str) or not run_id:
        return {"ok": False, "run_id": run_id, "archived": False}

    with _LOCK:
        current = _read_ids(task_dir)
        if run_id in current:
            # 已归档：幂等成功，无需回写。
            return {"ok": True, "run_id": run_id, "archived": True}
        current.append(run_id)
        return _write_ids(task_dir, current, run_id)


def _write_ids(task_dir: str, ids: List[str], run_id: str) -> dict:
    """原子写指定 run_id 集合到归档文件；失败返回 ok=False。"""
    path = resolve_archive_path(task_dir)
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({_KEY: ids}, fh, ensure_ascii=False, indent=2)
        return {"ok": True, "run_id": run_id, "archived": True}
    except OSError:
        return {"ok": False, "run_id": run_id, "archived": False}
