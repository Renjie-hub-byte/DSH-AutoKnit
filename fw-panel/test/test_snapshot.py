"""test_snapshot —— 读快照：load_snapshot/parse_snapshot 字段规整。"""

import os
import pytest

from autoknit_panel.snapshot import load_snapshot, parse_snapshot
from autoknit_panel.enums import STAGES, ROLES


@pytest.fixture
def snap_path():
    import conftest
    return conftest.snapshot_path()


@pytest.fixture
def contract_path():
    import conftest
    return conftest.snapshot_contract_path()


def test_load_snapshot_runner_shape(snap_path):
    snap = load_snapshot(snap_path)
    assert snap.run_id == "run-test-abc123"
    assert snap.task == "autoknit-v2-人在环上闭环"
    assert snap.status == "running"
    # runner 字段
    assert snap.modules == {"m01": "done", "m02": "done", "m03": "pending"}
    assert snap.completed_order == ["m01", "m02"]
    assert snap.done_module_ids == ["m01", "m02"]
    assert snap.module_tokens("m01") == 1200
    assert snap.module_tokens("m02") == 800
    # 未显式给 stage → 待 derive
    assert snap.stage == "" or snap.stage == "idle"


def test_load_snapshot_missing_raises(tmp_path):
    missing = os.path.join(tmp_path, "nope.json")
    with pytest.raises(FileNotFoundError):
        load_snapshot(missing)


def test_parse_snapshot_contract_shape(contract_path):
    import json
    with open(contract_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    snap = parse_snapshot(data)
    assert snap.stage == "audit"
    assert snap.roles == ["planner", "executor", "auditor"]
    assert snap.token_input == 5000
    assert snap.token_output == 1500
    assert snap.cache_hit is True


def test_parse_snapshot_normalizes_bad_enum():
    snap = parse_snapshot({"stage": "bogus", "roles": ["EXECUTOR", "auditor", "nope"]})
    assert snap.stage == "idle"
    assert snap.roles == ["executor", "auditor"]


def test_stage_and_role_enums_aligned():
    assert STAGES == ("planning", "exec", "audit", "split", "idle")
    assert ROLES == ("planner", "executor", "auditor")


def test_module_duration(snap_path):
    snap = load_snapshot(snap_path)
    # m01 ended 22:40:08 started 22:25:42 → 866s
    dur = snap.module_duration("m01")
    assert dur is not None and dur > 0
    # m03 ended null → None
    assert snap.module_duration("m03") is None
