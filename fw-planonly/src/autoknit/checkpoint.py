"""checkpoint 落盘。

规划完即停：把运行状态快照写到共享数据契约定义的 snapshot_store
(``总日志/快照.json``，支持 FW_SNAPSHOT_PATH 覆盖)，另写一份 plan_checkpoint.json
记录"规划已完成"的接续信息，供后续 ``autoknit run --resume-from-checkpoint`` 识别。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .models import TaskPlan
from .paths import TaskPaths

# 规划完成后进入 idle（已暂停等待真人审阅）；角色仅 planner。
POST_PLAN_STAGE = "idle"
POST_PLAN_ROLES = ["planner"]


def build_snapshot(plan: TaskPlan, token_input: int, token_output: int, cache_hit: str, pending: str) -> dict[str, Any]:
    """构造共享快照（对齐 snapshot_store columns）。"""
    return {
        "stage": POST_PLAN_STAGE,
        "roles": POST_PLAN_ROLES,
        "token_input": token_input,
        "token_output": token_output,
        "cache_hit": cache_hit,
        "pending": pending,
        # 扩展字段（data_shape 允许 extendable，供下游对接）
        "module_count": len(plan.modules),
        "task_name": plan.task_name,
        "timestamp": time.time(),
    }


def build_plan_checkpoint(plan: TaskPlan, task_yaml_path: str | Path) -> dict[str, Any]:
    """构造 plan 接续信息（供 run --resume-from-checkpoint 识别规划已完成）。"""
    return {
        "planned": True,
        "stage": POST_PLAN_STAGE,
        "task_yaml": str(Path(task_yaml_path)),
        "module_count": len(plan.modules),
        "task_name": plan.task_name,
        "timestamp": time.time(),
    }


def write_checkpoint(plan: TaskPlan, paths: TaskPaths, *, token_input: int = 0, token_output: int = 0,
                     cache_hit: str = "0", pending: str) -> list[Path]:
    """把快照与 plan checkpoint 一起落盘，返回写入路径列表。"""
    paths.ensure_log_dir()
    snapshot = build_snapshot(plan, token_input, token_output, cache_hit, pending)
    snapshot_path = paths.snapshot_path
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")

    plan_cp = build_plan_checkpoint(plan, paths.task_yaml)
    plan_cp_path = paths.plan_checkpoint_path
    plan_cp_path.write_text(json.dumps(plan_cp, ensure_ascii=False, indent=2), encoding="utf-8")
    return [snapshot_path, plan_cp_path]


def read_snapshot(paths: TaskPaths) -> dict[str, Any] | None:
    """读取快照。不存在返回 None（供摘要/接续判断）。"""
    path = paths.snapshot_path
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
