"""fwr_detail 兼容命名空间 —— 收敛自 dsh_cockpit m04（dsh.task.detail）。

转调 fw_api.task_detail。契约 path: dsh.task.detail。
用法不变：
    from fwr_detail import dsh
    dsh.task.detail(task_dir, run_id)
"""
from .task_detail import (  # noqa: F401
    detail,
    detail_from_raw,
    empty_result,
    dsh,
)

__all__ = ["detail", "detail_from_raw", "empty_result", "dsh"]
