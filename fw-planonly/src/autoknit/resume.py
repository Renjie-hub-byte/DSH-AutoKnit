"""run 接续：``--resume-from-checkpoint`` 识别规划 checkpoint，用同一 task.yaml 接上、不重复规划。

plan-only 规划完即停（stage=idle）；真人审完后由 ``autoknit run --resume-from-checkpoint``
把流程接下去。本模块读取 ``总日志/plan_checkpoint.json``（写于 plan-only），确认"已规划"后
加载同一份 task.yaml，记录一条接续事件（仍仅 planner / idle，绝不再跑规划），然后正常返回。
若目录尚未规划（无 checkpoint / 无 task.yaml），则确定性空降级并抛错。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import PlanNotReadyError
from .ledger import Ledger
from .models import TaskPlan
from .paths import TaskPaths
from .task_yaml import load_plan_from_task_yaml

# 与 checkpoint.py 中的接续标记保持一致（唯一事实来源在 checkpoint 写盘端）。
PLANNED_FLAG = "planned"


@dataclass
class ResumeResult:
    """一次 run 接续的结果。"""

    task_dir: Path
    plan: TaskPlan
    task_yaml_path: Path
    plan_checkpoint: dict[str, Any]
    events_path: Path
    tokens_path: Path
    ledger: Ledger = field(repr=False)


def read_plan_checkpoint(paths: TaskPaths) -> dict[str, Any] | None:
    """读取 plan checkpoint。文件缺失或 JSON 非法返回 None（供接续判断）。"""
    path = paths.plan_checkpoint_path
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _resolve_task_yaml(paths: TaskPaths, checkpoint: dict[str, Any] | None) -> Path:
    """优先用 checkpoint 记录的任务 yaml 路径，缺失则回退到任务目录的 task.yaml。"""
    recorded = checkpoint.get("task_yaml") if checkpoint else None
    if recorded:
        candidate = Path(recorded)
        if candidate.exists():
            return candidate
    return paths.task_yaml


def resume_from_checkpoint(task_dir: str | Path) -> ResumeResult:
    """从 plan checkpoint 接续 run。

    要求目录已经规划（存在 ``planned=True`` 的 checkpoint + 可解析的 task.yaml）。
    缺任一 → 确定性抛 :class:`PlanNotReadyError`（"尚未规划，无法接续"）。
    全程不重复规划、不产生 executor/auditor/split 事件、不发 LLM 请求。
    """
    paths = TaskPaths(task_dir)
    checkpoint = read_plan_checkpoint(paths)
    if checkpoint is None or not checkpoint.get(PLANNED_FLAG):
        raise PlanNotReadyError(
            f"任务目录尚未规划或缺少 plan checkpoint，无法 run --resume-from-checkpoint: "
            f"{paths.plan_checkpoint_path}"
        )

    task_yaml_path = _resolve_task_yaml(paths, checkpoint)
    if not task_yaml_path.exists():
        raise PlanNotReadyError(
            f"plan checkpoint 存在但找不到 task.yaml 无法接续: {task_yaml_path}"
        )
    plan = load_plan_from_task_yaml(task_yaml_path)

    # 接续事件：仍仅 planner / idle，明确"不重复规划"。
    ledger = Ledger(paths.events_path, paths.tokens_path, token_input=0, token_output=0)
    ledger.record("planner", "idle", "run_resumed",
                  f"从 plan checkpoint 接续（{len(plan.modules)} 模块），不重复规划")
    ledger.write(cache_hit="0")

    return ResumeResult(
        task_dir=paths.task_dir,
        plan=plan,
        task_yaml_path=task_yaml_path,
        plan_checkpoint=checkpoint,
        events_path=paths.events_path,
        tokens_path=paths.tokens_path,
        ledger=ledger,
    )
