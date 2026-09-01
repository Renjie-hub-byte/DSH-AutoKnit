"""一键暂停/继续控制模块。

面板给真人提供"暂停/继续"按钮。本模块把暂停/继续实现为**文件信号**，纯标准库、
不调任何 LLM、不触碰在跑模块的进程——这正是"暂停到当前节点结束，不打断在跑模块"
的落地方式：

  * 暂停 = 写入 pause 信号文件（``总日志/pause.json``）
  * 继续 = 删除该信号文件

**暂停语义（关键）**：暂停请求只对**尚未开始的节点**生效；正在运行的模块/节点
会自然跑完后再停下。调用方在"是否允许启动下一个节点"的边界处查询
``node_may_start()``，若返回 False 就原地停留、不再推进新的 LLM 会话。因此本模块
从设计上就不"打断在跑模块"，而是让跑完的节点停在边界上。

信号文件路径解析（与 ``answer.py`` 同款策略，作为面板自己的控制通道）：

    显式 path > 环境变量 ``FW_PAUSE_PATH`` > ``task_root/总日志/pause.json``

注：``data_contract`` 未定义 pause 存储，此处按面板控制通道新增，格式与既有
human_answer 存储并列（``总日志/`` 下同名规则），不覆盖/不冲突任何契约存储。
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# 默认存储相对路径（与契约 human_answer 同目录，作为面板控制通道）
DEFAULT_PAUSE_RELPATH = os.path.join("总日志", "pause.json")
PAUSE_ENV_VAR = "FW_PAUSE_PATH"

# pause.json 内固定的状态字段取值（与 data_contract 风格一致，真值用 "paused"）
PAUSED_FLAG: str = "paused"
RESUMED_FLAG: str = "resumed"


class ControlError(ValueError):
    """暂停/继续控制参数非法或读写失败。"""


# ---------------------------------------------------------------------------
# 路径解析
# ---------------------------------------------------------------------------
def resolve_pause_path(path: Optional[str] = None, task_root: Optional[str] = None) -> str:
    """解析 pause 信号文件的绝对路径。

    优先级：显式 ``path`` > 环境变量 ``FW_PAUSE_PATH`` >
    ``task_root/总日志/pause.json``。task_root 缺省用 ``TASK_ROOT`` 环境变量。
    """
    if path:
        return os.path.abspath(path)
    env_path = os.environ.get(PAUSE_ENV_VAR)
    if env_path:
        return os.path.abspath(env_path)
    root = task_root or os.environ.get("TASK_ROOT")
    if root:
        return os.path.abspath(os.path.join(root, DEFAULT_PAUSE_RELPATH))
    return os.path.abspath(DEFAULT_PAUSE_RELPATH)


def _atomic_write_json(path: str, record: Dict[str, Any]) -> None:
    """临时文件 + rename 原子写，避免半截 JSON 被对侧读到。"""
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=os.path.basename(path) + ".", suffix=".tmp", dir=parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(record, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


# ---------------------------------------------------------------------------
# 暂停 / 继续
# ---------------------------------------------------------------------------
def request_pause(
    reason: Optional[str] = None,
    path: Optional[str] = None,
    task_root: Optional[str] = None,
) -> Dict[str, Any]:
    """请求暂停：写入 pause 信号文件（暂停到当前节点结束，不打断在跑模块）。

    Args:
        reason: 可选，真人填写的暂停原因。
        path: 显式信号路径（默认按契约式解析）。
        task_root: 任务根目录。

    Returns:
        ``{"paused": True, "path": ..., "requested_at": ..., "reason": ...}``
    """
    target = resolve_pause_path(path, task_root)
    record: Dict[str, Any] = {
        "state": PAUSED_FLAG,
        "paused": True,
        "requested_at": _utc_now(),
    }
    if reason:
        record["reason"] = reason
    _atomic_write_json(target, record)
    return {
        "paused": True,
        "path": target,
        "requested_at": record["requested_at"],
        "reason": reason,
    }


def request_resume(
    path: Optional[str] = None,
    task_root: Optional[str] = None,
) -> Dict[str, Any]:
    """继续：删除 pause 信号文件，解除阻塞边界。

    Returns:
        ``{"paused": False, "path": ..., "resumed": True}``
    """
    target = resolve_pause_path(path, task_root)
    if os.path.exists(target):
        try:
            os.remove(target)
        except OSError as exc:
            raise ControlError(f"删除暂停信号失败 {target!r}: {exc}") from exc
    return {"paused": False, "path": target, "resumed": True}


def is_paused(path: Optional[str] = None, task_root: Optional[str] = None) -> bool:
    """当前是否处于暂停态（存在 pause 信号文件且标记为暂停）。"""
    target = resolve_pause_path(path, task_root)
    if not os.path.exists(target):
        return False
    try:
        with open(target, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        # 信号文件损坏/半截：保守视为暂停，避免在异常态下继续推进
        return True
    if not isinstance(data, dict):
        return True
    # 显式 paused:true 或缺失字段但文件存在 → 视为暂停
    return data.get("paused", True) is True


def pause_state(path: Optional[str] = None, task_root: Optional[str] = None) -> Dict[str, Any]:
    """拼暂停/继续状态块（可并入 dsh.panel.state 载荷）。

    Returns:
        ``{"paused": bool, "state": "paused"|"resumed", "path": ..., "reason": ...}``
    """
    target = resolve_pause_path(path, task_root)
    paused = is_paused(target)
    reason = None
    if paused and os.path.exists(target):
        try:
            with open(target, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                reason = data.get("reason")
        except (OSError, ValueError):
            reason = None
    return {
        "paused": paused,
        "state": PAUSED_FLAG if paused else RESUMED_FLAG,
        "path": target,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# 节点边界语义（暂停到当前节点结束，不打断在跑模块）
# ---------------------------------------------------------------------------
def node_may_start(
    paused: Optional[bool] = None,
    path: Optional[str] = None,
    task_root: Optional[str] = None,
) -> bool:
    """在"是否允许启动下一个节点"边界处查询；暂停时不启动新节点。

    Args:
        paused: 显式传入暂停态；不传则按 ``path`` 读盘判断。
        path/task_root: 读信号路径参数。

    Returns:
        False = 当前处于暂停态，**不应**启动新节点（让正在跑的模块自然跑完后停下）；
        True = 可以启动新节点。
    """
    if paused is None:
        paused = is_paused(path=path, task_root=task_root)
    return not paused


def pause_boundary(
    paused: Optional[bool] = None,
    path: Optional[str] = None,
    task_root: Optional[str] = None,
) -> Dict[str, Any]:
    """节点边界判定块（供面板/runner 在每节点入口处查询）。

    Returns:
        ``{"may_start": bool, "paused": bool, "reason": ...}`` —— may_start 为 False
        时表示应停在当前节点结束处，不再推进新的 LLM 会话。
    """
    state = pause_state(path=path, task_root=task_root) if paused is None else {
        "paused": paused, "state": PAUSED_FLAG if paused else RESUMED_FLAG,
        "path": path, "reason": None,
    }
    return {
        "may_start": node_may_start(state["paused"]),
        "paused": state["paused"],
        "reason": state.get("reason"),
    }


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------
def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
