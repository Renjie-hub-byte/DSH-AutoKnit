"""dsh_task_list 兼容命名空间 —— 收敛自 dsh_cockpit m03（dsh.task.list）。

转调 fw_api.task_list_service。契约 path: dsh.task.list。
用法不变：
    from dsh_task_list import dsh
    dsh.task.list(task_dir)
"""
from .task_list_service import (  # noqa: F401
    list_tasks,
    list_from_status,
    assemble,
    empty_result,
    urgency_of,
    URGENCY_RANK,
    URGENCY_OTHER,
    URGENCY_UNKNOWN,
    dsh,
)

__all__ = ["list_tasks", "list_from_status", "assemble", "empty_result",
           "urgency_of", "URGENCY_RANK", "URGENCY_OTHER", "URGENCY_UNKNOWN", "dsh"]
