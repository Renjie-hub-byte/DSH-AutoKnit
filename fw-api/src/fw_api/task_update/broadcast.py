"""dsh_update.broadcast —— dsh.task.update 广播总线（m05 的 dsh.task.update push 实现）。

契约（contracts/api.yaml）：path=dsh.task.update, method=push
  —— R→F 任务状态变化广播（阶段变更/模块变更/needs_human 出现）。

事件驱动语义：本模块不做轮询；广播事件由"文件变化事件 → watcher.handle() →
classify → bus.push()"这条纯事件链产生（见 dsh_update.watcher）。本文件只负责
"推送"这个动作的最小载体：一个可订阅的广播总线（UpdateBus）。

广播事件 payload（执行期涌现样例，字段固定、确定性、不含时间戳等随机量）：
    {
      "api": "dsh.task.update",          # 契约接口标识
      "change_type": "stage|module|needs_human",   # 变化类型（本轮启发式，见 watcher）
      "source": "snapshot.json|dispatch.jsonl|modules",  # 触发源文件
      "kind": "modified|created|deleted",   # 原始文件变化事件类型（透传）
      "path": "snapshot.json",           # 变化文件相对 task_dir 路径（透传规范化结果）
      "task_dir": "<任务目录绝对路径>",
      "run_id": "run-...",               # 受影响 run（按活跃 run 逐条广播）
      "stage": "executor|auditor|switch|needs_human|...",  # 计算出的阶段键
      "stage_label": "executor 执行中",   # 阶段中文标签
      "phase": "executor",               # 原始阶段（透传）
      "status": "running",               # 原始状态（透传）
      "module": "m02",                   # 当前模块（透传）
    }

边界：不写任何任务状态文件；不声明/实现 dsh.task.list 与 dsh.task.detail。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable, Dict, List

# 契约接口路径（与 contracts/api.yaml 对齐）
API_PATH = "dsh.task.update"


class UpdateBus:
    """广播总线：subscribe 注册订阅者，push 把事件推送给全部订阅者。

    订阅者按注册顺序同步收到事件；单个订阅者抛异常不影响其它订阅者
    （广播语义下订阅者异常不回流到事件源，防御性兜底）。
    """

    def __init__(self) -> None:
        self._subscribers: List[Callable[[Dict[str, Any]], None]] = []

    def subscribe(self, fn: Callable[[Dict[str, Any]], None]) -> None:
        """注册订阅者（可调用对象）；重复注册同一 fn 不生效。"""
        if callable(fn) and fn not in self._subscribers:
            self._subscribers.append(fn)

    def unsubscribe(self, fn: Callable[[Dict[str, Any]], None]) -> None:
        """注销订阅者；未注册的 fn 为无操作。"""
        if fn in self._subscribers:
            self._subscribers.remove(fn)

    def push(self, event: Dict[str, Any]) -> None:
        """把事件推送给所有订阅者（同步、按注册顺序）。"""
        for fn in list(self._subscribers):
            try:
                fn(event)
            except Exception:
                # 订阅者异常不拖垮广播（防御性兜底，正常路径不应触发）
                continue

    def __len__(self) -> int:
        return len(self._subscribers)

    def __repr__(self) -> str:  # pragma: no cover - 仅调试用
        return "<UpdateBus subscribers=%d>" % len(self._subscribers)


# 模块级默认总线：供 dsh.task.update.push 命名空间与下游（m06）直接使用。
# 测试请使用独立 UpdateBus 实例，避免跨用例共享状态。
default_bus = UpdateBus()


def push(event: Dict[str, Any]) -> None:
    """向默认总线推送一条 dsh.task.update 广播事件（dsh.task.update.push 的实现）。"""
    default_bus.push(event)


# ---------------------------------------------------------------------------
# dsh.task.update 命名空间（对齐契约 path：dsh.task.update，method: push）
# ---------------------------------------------------------------------------

class _update:
    """dsh.task.update 命名空间：dsh.task.update.push(event)。"""

    push = staticmethod(push)


class _task:
    """dsh.task 命名空间：dsh.task.update.push(event)。"""

    update = _update


dsh = SimpleNamespace(task=_task)

__all__ = ["UpdateBus", "default_bus", "push", "dsh", "API_PATH"]
