"""checkpoint 与事件/token 账本测试。

对应验收 3（写 checkpoint、规划完即停；事件日志/token 账本确认无 executor/auditor/split 事件）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoknit.checkpoint import build_snapshot, read_snapshot
from autoknit.errors import AutoknitError
from autoknit.ledger import ALLOWED_ROLES, ALLOWED_STAGES, Ledger, LedgerViolationError
from autoknit.runner import run_plan_only


@pytest.fixture()
def planned_dir(tmp_path: Path) -> Path:
    d = tmp_path / "task"
    d.mkdir()
    (d / "PRD.md").write_text(
        "# T\n目标\n\n## A\n模块A\n\n## B\n模块B\n\n## C\n模块C\n", encoding="utf-8"
    )
    return d


def test_snapshot_aligned_with_contract(planned_dir: Path) -> None:
    run_plan_only(planned_dir)
    snap = read_snapshot(__import__("autoknit.paths", fromlist=["TaskPaths"]).TaskPaths(planned_dir))
    assert snap is not None
    assert snap["stage"] in {"planning", "exec", "audit", "split", "idle"}
    assert snap["roles"] == ["planner"]
    assert isinstance(snap["token_input"], int)
    assert isinstance(snap["token_output"], int)
    assert "pending" in snap


def test_snapshot_stage_is_idle_after_plan(planned_dir: Path) -> None:
    run_plan_only(planned_dir)
    from autoknit.paths import TaskPaths

    snap = read_snapshot(TaskPaths(planned_dir))
    assert snap["stage"] == "idle"  # 规划完即停


def test_events_ledger_has_no_executor_auditor_split(planned_dir: Path) -> None:
    run_plan_only(planned_dir)
    events_path = planned_dir / "总日志" / "events.jsonl"
    assert events_path.exists()
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert events, "事件日志应为空列表外的至少一条 planner 事件"
    roles = {e["role"] for e in events}
    stages = {e["stage"] for e in events}
    assert roles == {"planner"}
    assert not (roles & {"executor", "auditor"})
    assert not (stages & {"exec", "audit", "split"})


def test_token_ledger_confirms_no_llm_and_planner_only(planned_dir: Path) -> None:
    run_plan_only(planned_dir)
    tokens = json.loads((planned_dir / "总日志" / "tokens.json").read_text(encoding="utf-8"))
    assert tokens["llm_requests"] == 0
    assert tokens["roles_seen"] == ["planner"]
    assert tokens["forbidden_events"] == 0


def test_ledger_rejects_executor_event(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "events.jsonl", tmp_path / "tokens.json")
    with pytest.raises(LedgerViolationError):
        ledger.record("executor", "exec", "do_work")


def test_ledger_allowed_sets_match_shared_enums() -> None:
    assert ALLOWED_ROLES == {"planner"}
    assert ALLOWED_STAGES <= {"planning", "idle"}


def test_build_snapshot_structure() -> None:
    from autoknit.models import TaskPlan

    plan = TaskPlan(task_name="T", goal="", execution_order=[], modules=[])
    snap = build_snapshot(plan, token_input=0, token_output=0, cache_hit="0", pending="p")
    assert snap["stage"] == "idle"
    assert snap["roles"] == ["planner"]
