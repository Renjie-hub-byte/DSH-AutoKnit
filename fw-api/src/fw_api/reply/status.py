"""任务状态解析。

回复服务需要先确认目标任务处于 ``needs_human`` 可回复态，才允许写回复通道。
状态来源约定为任务目录内的元数据文件 ``task.json`` 的 ``status`` 字段。

本模块只读任务元数据，不修改 DSH 会话文件内部结构（边界）。
"""

from __future__ import annotations

import json
from pathlib import Path

# 任务状态元数据文件名。
STATUS_FILE = "task.json"
# 可回复态。
NEEDS_HUMAN = "needs_human"


def read_status(run_dir: str | Path) -> str | None:
    """读取任务目录的状态。

    返回 ``task.json`` 中的 ``status`` 字符串；文件缺失、解析失败或字段非字符串时
    返回 ``None``（视为"未知状态"，即非 needs_human）。不抛异常——状态探测失败
    属于确定性 error 路径，交由上层映射为 ``非needs_human``。
    """
    status_path = Path(run_dir) / STATUS_FILE
    if not status_path.is_file():
        return None
    try:
        data = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    status = data.get("status") if isinstance(data, dict) else None
    return status if isinstance(status, str) else None
