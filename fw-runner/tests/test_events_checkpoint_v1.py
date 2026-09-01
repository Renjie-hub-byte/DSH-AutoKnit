"""G1–G4：v1.0 事件类型常量 + checkpoint 快照 v4（split 字段 / running→pending / 旧快照识别）。"""
from __future__ import annotations

from fw_runner import events
from fw_runner.checkpoint import (
    SNAPSHOT_SCHEMA_V3,
    SNAPSHOT_SCHEMA_VERSION,
    build_snapshot,
    snapshot_schema_version,
    snapshot_to_state,
)
from fw_runner.context import load_task_context
from fw_runner.model import RunState

SPLIT_FIELDS = ("split_depth", "parent_module", "child_modules", "aggregated", "model_tier")


def test_g1_event_type_constants():
    """G1：5 个 v1.0 事件类型常量，与 runner 实际 emit 的事件名一致。"""
    assert events.EVENT_MODULE_SPLIT == "module.split"
    assert events.EVENT_MODULE_SPLIT_FAILED == "module.split_failed"
    assert events.EVENT_MODULE_AGGREGATED == "module.aggregated"
    assert events.EVENT_MODULE_MERGE_BACK == "module.merge_back"
    assert events.EVENT_MODULE_MODEL_UPGRADE == "module.model_upgrade"
    assert events.EVENT_MODULE_HUMAN_ABANDONED == "module.human_abandoned"
    assert events.EVENT_MODULE_HUMAN_RERUN == "module.human_rerun"
    assert events.V1_EVENT_TYPES == (
        "module.split", "module.split_failed", "module.aggregated",
        "module.merge_back", "module.model_upgrade",
        "module.human_abandoned", "module.human_rerun",
    )
    # 与 runner.py round_005 + human.py round_010 实际 emit 的事件名对齐
    expected_emit = {
        "module.split", "module.split_failed", "module.aggregated",
        "module.merge_back", "module.model_upgrade",
        "module.human_abandoned", "module.human_rerun",
    }
    assert set(events.V1_EVENT_TYPES) == expected_emit
    # 无重复：元组内事件名不重不漏
    assert len(events.V1_EVENT_TYPES) == len(set(events.V1_EVENT_TYPES))


def test_g1_human_events_enumerable_in_v1_event_types():
    """round_010 观察项：H 轮实际 emit 的两个 human 事件名在 V1_EVENT_TYPES 中可枚举。"""
    assert "module.human_abandoned" in events.V1_EVENT_TYPES
    assert "module.human_rerun" in events.V1_EVENT_TYPES
    assert events.EVENT_MODULE_HUMAN_ABANDONED in events.V1_EVENT_TYPES
    assert events.EVENT_MODULE_HUMAN_RERUN in events.V1_EVENT_TYPES


def test_g2_snapshot_per_module_split_fields(chain_root):
    """G2：快照 per_module 含 split_depth/parent_module/child_modules/aggregated/model_tier。"""
    ctx = load_task_context(chain_root)
    st = RunState()
    st.ensure("m01").split_depth = 1
    st.ensure("m01").parent_module = "m00"
    st.ensure("m01").child_modules = ["m01a", "m01b"]
    st.ensure("m01").aggregated = True
    st.ensure("m01").model_tier = 1
    st.ensure("m01").partial_count = 2

    doc = build_snapshot(ctx, st, "running", "test")
    pm = doc["per_module"]["m01"]
    for key in SPLIT_FIELDS + ("partial_count",):
        assert key in pm, key
    assert pm["split_depth"] == 1
    assert pm["parent_module"] == "m00"
    assert pm["child_modules"] == ["m01a", "m01b"]
    assert pm["aggregated"] is True
    assert pm["model_tier"] == 1

    # 往返：snapshot_to_state 恢复这些字段
    restored = snapshot_to_state(ctx, doc)
    r = restored.per_module["m01"]
    assert r.split_depth == 1
    assert r.parent_module == "m00"
    assert r.child_modules == ["m01a", "m01b"]
    assert r.aggregated is True
    assert r.model_tier == 1
    assert r.partial_count == 2


def test_g3_running_to_pending_on_restore(chain_root):
    """G3：恢复时 running → pending（崩溃恢复重新执行）。"""
    ctx = load_task_context(chain_root)
    st = RunState()
    st.modules = {"m01": "running", "m02": "done"}
    doc = build_snapshot(ctx, st, "interrupted", "crash")
    restored = snapshot_to_state(ctx, doc)
    assert restored.modules["m01"] == "pending"
    assert restored.modules["m02"] == "done"


def test_g4_schema_version_bumped_and_old_snapshot_recognized(chain_root):
    """G4：schema v3→v4；旧 v3 快照可识别并加载（缺字段默认兜底，A6 兼容）。"""
    assert SNAPSHOT_SCHEMA_VERSION == 4
    assert SNAPSHOT_SCHEMA_V3 == 3

    ctx = load_task_context(chain_root)
    old_v3 = {
        "schema_version": 3,
        "run_id": "run-old",
        "status": "running",
        "cause": "",
        "modules": {"m01": "running", "m02": "done"},
        "failure_counts": {"m01": 2, "m02": 0},
        "per_module": {  # 旧 v3 快照：无任何 v1.0 split/模型字段
            "m01": {"executor_round": 3, "block_count": 2},
        },
    }
    assert snapshot_schema_version(old_v3) == 3          # 识别为 v3
    assert snapshot_schema_version({"schema_version": 4}) == 4  # 识别为 v4
    assert snapshot_schema_version({}) == 0              # 缺字段兜底

    restored = snapshot_to_state(ctx, old_v3)
    assert restored.run_id == "run-old"
    # G3：旧快照里 running 的 m01 也重置为 pending
    assert restored.modules["m01"] == "pending"
    assert restored.modules["m02"] == "done"
    assert restored.failure_counts["m01"] == 2
    # A6/G4：缺字段默认兜底
    pm = restored.per_module["m01"]
    assert pm.split_depth == 0
    assert pm.parent_module == ""
    assert pm.child_modules == []
    assert pm.aggregated is False
    assert pm.model_tier == 0
    assert pm.executor_round == 3  # 既有字段保留
    assert pm.block_count == 2
