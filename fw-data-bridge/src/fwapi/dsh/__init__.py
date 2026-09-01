"""fwapi.dsh —— 任务数据访问子包。

对应 fw-api 的 dsh 命名空间。实现：
- task.list / task.detail / task.tree / task.timeline（只读任务数据源，见 task.py）
- reply（人工决策回复写入 needs_human/reply.md，见 reply.py）
- usage.summary（消耗汇总）/ usage.run_usage（run 级 + per-module token 拆分，见 usage.py）
- events（dsh.task.update 事件桥接，可选，见 events.py）
"""

from fwapi.dsh import events, reply, task, usage  # noqa: F401

__all__ = ["task", "reply", "usage", "events"]
