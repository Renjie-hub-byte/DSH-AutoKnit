"""checkpoint 快照：字段齐全 / 原子写 / resume 续接计数。"""
from __future__ import annotations

import json

from fw_runner.checkpoint import build_snapshot, read_snapshot, snapshot_to_state, write_checkpoint
from fw_runner.context import load_task_context
from fw_runner.model import RunState
from fw_runner.runner import run


def test_snapshot_fields_after_complete(indep4_root, harness):
    run(indep4_root, executor_driver=harness.make_executor(),
        auditor_driver=harness.make_auditor())
    snap = read_snapshot(indep4_root)
    assert snap is not None
    # 需求要求快照含：模块状态 / 依赖图 / 失败计数
    assert set(snap["modules"].keys()) == {"m01", "m02", "m03", "m04"}
    assert snap["dependencies"] == {"m01": [], "m02": [], "m03": [], "m04": []}
    assert snap["failure_counts"] == {"m01": 0, "m02": 0, "m03": 0, "m04": 0}
    assert snap["status"] == "complete"
    assert snap["schema_version"] == 4
    # 每模块完成即写（checkpoint_every=1）：per_module 计数持久化
    assert snap["per_module"]["m01"]["executor_round"] == 1
    assert snap["per_module"]["m01"]["auditor_round"] == 1
    assert snap["per_module"]["m01"]["executor_id"] == "E1"
    # G2：per_module 含 v1.0 split/模型字段
    for key in ("split_depth", "parent_module", "child_modules",
                "partial_count", "aggregated", "model_tier"):
        assert key in snap["per_module"]["m01"], key
    assert "last_seq" in snap and snap["last_seq"] > 0
    assert "budget_used_tokens" in snap


def test_snapshot_roundtrip_resume_state(chain_root):
    ctx = load_task_context(chain_root)
    st = RunState()
    st.run_id = "run-x"
    st.modules = {"m01": "done", "m02": "running"}
    st.completed_order = ["m01"]
    st.last_seq = 7
    st.budget_used_tokens = 42
    st.ensure("m01").executor_round = 1
    st.ensure("m02").executor_round = 2
    st.ensure("m02").block_total = 1

    write_checkpoint(ctx, st, "running", "test")
    snap = read_snapshot(chain_root)
    assert snap["status"] == "running"
    restored = snapshot_to_state(ctx, snap)
    assert restored.run_id == "run-x"
    assert restored.modules == {"m01": "done", "m02": "pending"}  # G3: running→pending 崩溃恢复
    assert restored.completed_order == ["m01"]
    assert restored.last_seq == 7
    assert restored.budget_used_tokens == 42
    assert restored.per_module["m01"].executor_round == 1
    assert restored.per_module["m02"].executor_round == 2
    assert restored.per_module["m02"].block_total == 1


def test_build_snapshot_dependencies_from_task(chain_root):
    ctx = load_task_context(chain_root)
    st = RunState()
    doc = build_snapshot(ctx, st, "running", "test")
    assert doc["dependencies"] == {"m01": [], "m02": ["m01"]}


def test_checkpoint_is_valid_json_atomic(indep4_root, harness):
    run(indep4_root, executor_driver=harness.make_executor(),
        auditor_driver=harness.make_auditor())
    raw = (indep4_root / "总日志" / "快照.json").read_text(encoding="utf-8")
    json.loads(raw)  # 完整 JSON（非半截）
    assert (indep4_root / "总日志" / "快照.json").is_file()
