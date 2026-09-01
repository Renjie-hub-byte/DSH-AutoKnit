"""v1.0 数据模型：三态判定 / split 状态 / A3-A5 新字段 / 旧快照兼容（A1-A6）。"""
from __future__ import annotations

from fw_runner.model import (
    MODULE_STATUS_OPTIONS,
    VERDICTS,
    DriverOutcome,
    ModuleAgentState,
    RunConfig,
)


def test_verdicts_three_state():
    assert VERDICTS == ("pass", "partial", "block")


def test_module_status_has_split():
    assert "split" in MODULE_STATUS_OPTIONS


def test_module_agent_state_new_field_defaults():
    s = ModuleAgentState()
    assert s.split_depth == 0
    assert s.parent_module == ""
    assert s.child_modules == []
    assert s.partial_count == 0
    assert s.aggregated is False
    assert s.model_tier == 0


def test_module_agent_state_roundtrip_preserves_new_fields():
    s = ModuleAgentState()
    s.split_depth = 2
    s.parent_module = "m01"
    s.child_modules = ["m01a", "m01b"]
    s.partial_count = 3
    s.aggregated = True
    s.model_tier = 1
    r = ModuleAgentState.from_dict(s.to_dict())
    assert r.split_depth == 2
    assert r.parent_module == "m01"
    assert r.child_modules == ["m01a", "m01b"]
    assert r.partial_count == 3
    assert r.aggregated is True
    assert r.model_tier == 1


def test_module_agent_state_from_dict_old_snapshot_defaults():
    # v0.4 旧快照 per_module 无 split/pro 兜底字段 → 默认值兜底（A6）
    old = {"executor_round": 1, "auditor_round": 1, "executor_id": "E1",
           "block_count": 0, "block_total": 1}
    s = ModuleAgentState.from_dict(old)
    assert s.executor_round == 1
    assert s.executor_id == "E1"
    assert s.split_depth == 0
    assert s.parent_module == ""
    assert s.child_modules == []
    assert s.partial_count == 0
    assert s.aggregated is False
    assert s.model_tier == 0


def test_driver_outcome_new_field_defaults():
    o = DriverOutcome()
    assert o.passed_count == 0
    assert o.total_count == 0
    assert o.remaining_items == []


def test_driver_outcome_from_mapping_new_fields():
    o = DriverOutcome.from_mapping({
        "verdict": "partial",
        "passed_count": 3,
        "total_count": 4,
        "remaining_items": ["前端页面未实现"],
    })
    assert o.verdict == "partial"
    assert o.passed_count == 3
    assert o.total_count == 4
    assert o.remaining_items == ["前端页面未实现"]


def test_driver_outcome_from_mapping_missing_counts_defaults():
    # 旧 auditor 结果缺计数字段 → 默认值兜底
    o = DriverOutcome.from_mapping({"verdict": "block"})
    assert o.passed_count == 0
    assert o.total_count == 0
    assert o.remaining_items == []


def test_run_config_new_field_defaults():
    cfg = RunConfig()
    assert cfg.enable_split is True
    assert cfg.split_max_depth == 2
    assert cfg.split_min_deliverables == 2
    assert cfg.split_merge_after_fails == 3
    assert cfg.enable_fallback_model is True
    assert cfg.fallback_model == "pro"
    assert cfg.model_tiers == ["flash", "pro"]


def test_run_config_to_dict_includes_v1_fields():
    d = RunConfig().to_dict()
    assert d["enable_split"] is True
    assert d["split_max_depth"] == 2
    assert d["split_min_deliverables"] == 2
    assert d["split_merge_after_fails"] == 3
    assert d["enable_fallback_model"] is True
    assert d["fallback_model"] == "pro"
    assert d["model_tiers"] == ["flash", "pro"]
