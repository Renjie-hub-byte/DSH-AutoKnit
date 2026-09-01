"""摘要计算与对外只读接口 dsh.plan-only.summary。

:func:`get_plan_summary` 是契约接口 ``dsh.plan-only.summary (get)`` 的实现：
读 task.yaml 返回 data_shape 规定的列表；任务目录缺 task.yaml 时确定性空降级并抛错。
:func:`format_summary_text` 负责把摘要渲染成 stdout 可读文本。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .errors import PlanNotReadyError
from .models import TaskPlan
from .task_yaml import load_plan_from_task_yaml


def get_plan_summary(plan: TaskPlan) -> list[dict[str, Any]]:
    """把规划转成契约要求的摘要列表。

    返回项形如 ``{'module_name': str, 'estimated_lines': int, 'first_block_lines': int}``。
    """
    return [
        {
            "module_name": module.name,
            "estimated_lines": module.estimated_lines,
            "first_block_lines": module.first_block.estimate_lines,
        }
        for module in plan.modules
    ]


def load_plan_summary(task_dir: str | Path) -> list[dict[str, Any]]:
    """从任务目录加载规划并生成摘要；缺 task.yaml 时确定性空降级并报错。"""
    task_yaml_path = Path(task_dir) / "task.yaml"
    if not task_yaml_path.exists():
        raise PlanNotReadyError(
            f"任务目录缺少 task.yaml（尚未规划或已清理）: {task_yaml_path}"
        )
    plan = load_plan_from_task_yaml(task_yaml_path)
    return get_plan_summary(plan)


def format_summary_text(summary: list[dict[str, Any]]) -> str:
    """渲染可读摘要文本（stdout 输出用）。

    包含模块总数、每模块预计行数 + 首个 executor 任务行数，以及预计总行数汇总。
    空列表时给出确定性提示（PRD 未识别到可拆解段落）。
    """
    total_lines = sum(item.get("estimated_lines", 0) for item in summary)
    lines = [f"plan-only 规划摘要：共 {len(summary)} 个大模块，预计总行数 {total_lines}", ""]
    if not summary:
        lines.append("  （无模块——PRD 中未识别到可拆解段落）")
    for item in summary:
        lines.append(
            f"  - {item['module_name']}: 预计 {item['estimated_lines']} 行, "
            f"首个 executor 任务 {item['first_block_lines']} 行"
        )
    lines.append("")
    return "\n".join(lines)
