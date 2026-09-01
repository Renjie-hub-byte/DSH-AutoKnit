"""test_decision —— 待决策信息展示模块单测。

覆盖验收第 1 条：读 human_pending/事件拼出待决策类型
(auditor 连续打回 / split 歧义 / 外部信息请求 / end-gate) 与预定义选项 A/B/C/D。
"""

import pytest

from autoknit_panel import decision as d


# ---------------------------------------------------------------------------
# 枚举/常量
# ---------------------------------------------------------------------------
def test_decision_kinds_cover_all_four_types():
    assert set(d.DECISION_KINDS) == {
        "auditor_reject",
        "split_ambiguity",
        "external_request",
        "end_gate",
    }


def test_kind_labels_exist_for_all_kinds():
    for kind in d.DECISION_KINDS:
        assert d.KIND_LABELS[kind]


def test_standard_options_are_a_b_c_d():
    assert d.standard_options() == ["A", "B", "C", "D"]
    assert d.STANDARD_OPTIONS == ("A", "B", "C", "D")


def test_pending_decision_default_options_are_a_b_c_d():
    pd = d.PendingDecision(kind="external_request", message="x")
    assert pd.options == ("A", "B", "C", "D")
    assert pd.label == "外部信息请求"


# ---------------------------------------------------------------------------
# 事件流推断
# ---------------------------------------------------------------------------
def test_auditor_reject_from_two_consecutive_rejects():
    events = [
        {"role": "auditor", "module": "m02", "event_type": "audit", "outcome": "reject"},
        {"role": "auditor", "module": "m02", "event_type": "audit", "outcome": "reject"},
    ]
    result = d.classify_pending(events=events)
    kinds = [p.kind for p in result]
    assert "auditor_reject" in kinds
    auditor = [p for p in result if p.kind == "auditor_reject"]
    assert auditor and auditor[0].module_id == "m02"


def test_auditor_reject_not_triggered_by_single_reject():
    events = [
        {"role": "auditor", "module": "m02", "event_type": "audit", "outcome": "reject"},
    ]
    result = d.classify_pending(events=events)
    assert not [p for p in result if p.kind == "auditor_reject"]


def test_split_ambiguity_from_event():
    events = [
        {
            "role": "executor",
            "module": "m03",
            "event_type": "split",
            "needs_human": True,
            "message": "split 存在歧义，需要真人裁定拆分方式",
        },
    ]
    result = d.classify_pending(events=events)
    assert any(p.kind == "split_ambiguity" for p in result)


def test_external_request_from_needs_human_event():
    events = [
        {
            "role": "executor",
            "module": "m05",
            "event_type": "human_request",
            "needs_human": True,
            "message": "需要外部接入信息才能继续",
        },
    ]
    result = d.classify_pending(events=events)
    assert any(p.kind == "external_request" for p in result)


def test_end_gate_from_explicit_kind_event():
    events = [
        {
            "role": "runner",
            "event_type": "end_gate",
            "needs_human": True,
            "kind": "end_gate",
            "message": "end-gate 收尾确认",
        },
    ]
    result = d.classify_pending(events=events)
    assert any(p.kind == "end_gate" for p in result)


# ---------------------------------------------------------------------------
# human_pending 推断
# ---------------------------------------------------------------------------
def test_human_pending_external_request():
    pending = [{"needs_human": True, "message": "外部信息请求，请补充上下文"}]
    result = d.classify_pending(human_pending=pending)
    assert any(p.kind == "external_request" for p in result)


def test_human_pending_reject_marker_is_auditor_reject():
    pending = [{"needs_human": True, "kind": "auditor_reject", "module_id": "m01"}]
    result = d.classify_pending(human_pending=pending)
    found = [p for p in result if p.kind == "auditor_reject"]
    assert found and found[0].module_id == "m01"


def test_human_pending_split_marker():
    pending = [{"needs_human": True, "message": "split 拆分歧义"}]
    result = d.classify_pending(human_pending=pending)
    assert any(p.kind == "split_ambiguity" for p in result)


# ---------------------------------------------------------------------------
# 快照兜底：idle → end-gate
# ---------------------------------------------------------------------------
def test_idle_snapshot_produces_end_gate_fallback():
    snapshot = {"stage": "idle", "roles": ["planner"]}
    result = d.classify_pending(snapshot=snapshot)
    assert any(p.kind == "end_gate" for p in result)


def test_non_idle_snapshot_no_fallback():
    snapshot = {"stage": "exec", "roles": ["executor"]}
    result = d.classify_pending(snapshot=snapshot)
    assert not [p for p in result if p.kind == "end_gate"]


# ---------------------------------------------------------------------------
# 去重与面板载荷
# ---------------------------------------------------------------------------
def test_build_pending_decision_block_fields():
    events = [
        {"role": "auditor", "module": "m02", "outcome": "reject"},
        {"role": "auditor", "module": "m02", "outcome": "reject"},
    ]
    block = d.build_pending_decision(events=events)
    assert set(block) == {"blocked", "count", "items"}
    assert block["blocked"] is True
    assert block["count"] >= 1
    item = block["items"][0]
    assert item["kind"] == "auditor_reject"
    assert item["options"] == ["A", "B", "C", "D"]
    assert item["needs_human"] is True
    assert item["module_id"] == "m02"


def test_build_pending_decision_empty_block():
    block = d.build_pending_decision()
    assert block == {"blocked": False, "count": 0, "items": []}


def test_dedupe_same_kind_module():
    pending = [
        {"needs_human": True, "kind": "external_request", "module_id": "m07"},
        {"needs_human": True, "kind": "external_request", "module_id": "m07"},
    ]
    result = d.classify_pending(human_pending=pending)
    ext = [p for p in result if p.kind == "external_request"]
    assert len(ext) == 1
