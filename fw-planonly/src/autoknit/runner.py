"""plan-only 流程编排（runner 的"只跑 planner、停掉模块执行"开关落地）。

:func:`run_plan_only` 把 PRD 解析 -> 规划 -> task.yaml 落盘 -> 事件/账本 -> checkpoint
串起来，作为 CLI 与 API 的公共入口。整条链路不产生 executor/auditor/split 事件、
不发任何 LLM 请求（token 账本恒为 0）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .checkpoint import write_checkpoint
from .errors import NoPrdFoundError
from .ledger import Ledger
from .models import TaskPlan
from .paths import TaskPaths
from .planner import plan
from .prd_parser import parse_prd
from .summary import get_plan_summary
from .task_yaml import save_task_yaml


@dataclass
class PlanOnlyResult:
    plan: TaskPlan
    summary: list[dict[str, Any]]
    task_yaml_path: Path
    checkpoint_paths: list[Path]
    events_path: Path
    tokens_path: Path
    ledger: Ledger = field(repr=False)

    def module_count(self) -> int:
        return len(self.plan.modules)


def resolve_prd_path(paths: TaskPaths, prd_override: str | Path | None) -> Path:
    """确定性解析 PRD：--prd 优先，否则按搜索顺序在任务目录内找。找不到抛错。"""
    if prd_override is not None:
        candidate = Path(prd_override)
        if not os.path.isabs(str(candidate)):
            candidate = paths.task_dir / candidate
        candidate = candidate.resolve()
        if not candidate.exists():
            raise NoPrdFoundError(f"指定的 PRD 不存在: {candidate}")
        return candidate

    for name in paths.prd_candidates:
        candidate = (paths.task_dir / name).resolve()
        if candidate.exists():
            return candidate

    # 任务-*.md 或目录内唯一 .md。
    md_files = sorted(paths.task_dir.glob("任务-*.md")) if paths.task_dir.is_dir() else []
    if not md_files:
        md_files = sorted(paths.task_dir.glob("*.md")) if paths.task_dir.is_dir() else []
    if len(md_files) == 1:
        return md_files[0]
    if len(md_files) > 1:
        raise NoPrdFoundError(
            "任务目录存在多个候选 PRD，请用 --prd 显式指定: " + ", ".join(p.name for p in md_files)
        )
    raise NoPrdFoundError(f"任务目录中找不到 PRD（{paths.task_dir}）")


def run_plan_only(
    task_dir: str | Path,
    prd_override: str | Path | None = None,
    pending: str = "awaiting human review of plan",
) -> PlanOnlyResult:
    """执行一轮 plan-only。规划完即停，正常返回（不发 LLM、无执行类事件）。"""
    paths = TaskPaths(task_dir)
    paths.ensure_log_dir()
    prd_path = resolve_prd_path(paths, prd_override)
    text = prd_path.read_text(encoding="utf-8")
    parsed = parse_prd(text)
    task_plan = plan(parsed)

    # 1. 产出 task.yaml
    task_yaml_path = save_task_yaml(task_plan, paths.task_yaml)

    # 2. 事件账本（仅 planner / planning-idle，写盘即"停掉模块执行"的确定性证据）
    ledger = Ledger(paths.events_path, paths.tokens_path, token_input=0, token_output=0)
    ledger.record("planner", "planning", "plan_started", f"从 PRD 规划: {parsed.title} ({len(task_plan.modules)} 模块)")
    ledger.record("planner", "planning", "plan_saved", f"task.yaml 已写入: {task_yaml_path.name}")
    ledger.record("planner", "idle", "plan_finished", "规划完成，plan-only 停住（不执行、不审计、不拆分）")
    ledger.write(cache_hit="0")

    # 3. checkpoint（规划完即停）
    checkpoint_paths = write_checkpoint(task_plan, paths, token_input=ledger.token_input,
                                        token_output=ledger.token_output, cache_hit="0", pending=pending)

    # 4. 摘要
    summary = get_plan_summary(task_plan)

    return PlanOnlyResult(
        plan=task_plan,
        summary=summary,
        task_yaml_path=task_yaml_path,
        checkpoint_paths=checkpoint_paths,
        events_path=paths.events_path,
        tokens_path=paths.tokens_path,
        ledger=ledger,
    )
