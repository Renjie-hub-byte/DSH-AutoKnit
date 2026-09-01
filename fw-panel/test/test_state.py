"""test_state —— 面板状态拼装：当前阶段 + 各角色 token 消耗（字段断言）。"""

import pytest

from autoknit_panel.snapshot import load_snapshot, parse_snapshot
from autoknit_panel.events import load_events
from autoknit_panel.state import build_panel_state, derive_stage, derive_roles
from autoknit_panel.enums import ROLES


@pytest.fixture
def snap():
    import conftest
    return load_snapshot(conftest.snapshot_path())


@pytest.fixture
def events():
    import conftest
    return load_events(conftest.dispatch_path())


def test_state_derives_stage_from_events(snap, events):
    # 事件流最近角色事件为 m02 的 auditor.round.start → stage=audit
    assert derive_stage(snap, events) == "audit"


def test_state_roles_derived(snap, events):
    roles = derive_roles(snap, events)
    assert isinstance(roles, list)
    assert all(r in ROLES for r in roles)
    assert "executor" in roles and "auditor" in roles


def test_state_consumption_fields(snap, events):
    state = build_panel_state(snap, events)
    cons = state["consumption"]
    # 顶层字段
    for key in ("token_input", "token_output", "cache_hit", "duration_s"):
        assert key in cons, f"consumption 缺字段 {key}"
    assert isinstance(cons["token_input"], int)
    assert isinstance(cons["token_output"], int)
    assert isinstance(cons["cache_hit"], bool)
    assert isinstance(cons["duration_s"], float)
    # 各角色消耗字段
    for role in ROLES:
        pc = cons["per_role"][role]
        for key in ("token_input", "token_output", "cache_hit", "duration_s"):
            assert key in pc, f"per_role[{role}] 缺字段 {key}"
    # runner-shape 无显式 I/O → token 总量来自 per_module.tokens_used
    assert cons["token_input"] > 0


def test_state_explicit_contract_shape(events):
    import conftest
    snap = load_snapshot(conftest.snapshot_contract_path())
    state = build_panel_state(snap, events)
    assert state["stage"] == "audit"          # 显式 stage
    assert state["consumption"]["token_input"] == 5000
    assert state["consumption"]["token_output"] == 1500
    assert state["consumption"]["cache_hit"] is True
    assert set(state["roles"]) == {"planner", "executor", "auditor"}


def test_state_stage_idle_when_done(events):
    snap = parse_snapshot({"modules": {"m01": "done", "m02": "done"}, "status": "running"})
    assert derive_stage(snap, events) == "idle"


def test_state_stage_idle_when_needs_human(events):
    snap = parse_snapshot({"modules": {"m01": "running"}, "status": "running",
                           "needs_human": ["等待真人决策"]})
    assert derive_stage(snap, events) == "idle"


def test_state_has_pending_and_progress(snap, events):
    import conftest
    state = build_panel_state(snap, events, pending_path=conftest.human_pending_path())
    assert "pending" in state and len(state["pending"]) == 2
    assert "progress" in state
    assert state["updated_at"] == "2026-08-26T22:47:01+08:00"
