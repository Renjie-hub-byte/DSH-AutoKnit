"""对外公开 API。

供真人 / 另一 agent / DSH 面板对接。契约接口 ``dsh.plan-only.summary (get)``
由 :func:`get_plan_summary` 实现——读 task.yaml 返回摘要列表，
缺 task.yaml 时确定性空降级并抛 :class:`PlanNotReadyError`。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .summary import load_plan_summary

__all__ = ["get_plan_summary"]


def get_plan_summary(task_dir: str | Path) -> list[dict[str, Any]]:
    """契约 dsh.plan-only.summary 的实现。

    返回形如 ``[{'module_name', 'estimated_lines', 'first_block_lines'}]`` 的列表；
    任务目录缺 task.yaml 时确定性空降级（抛 PlanNotReadyError）。
    """
    return load_plan_summary(task_dir)
