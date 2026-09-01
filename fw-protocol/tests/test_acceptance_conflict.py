"""验收冲突检测：'快 vs 安全' 关键词冲突 → 标记为需人工定优先级（conflict，非 error）。"""
from conftest import make_module, make_task

from fw_protocol import validate_document


def _conflicts(result):
    return list(result.conflicts)


def test_speed_safety_conflict_flagged():
    doc = make_task([
        make_module("m01", objective="风控实时判定",
                    acceptance=["响应要快，越快越好，性能优先",
                                "安全第一，模型判定万无一失，宁可慢也不出错"]),
    ])
    result = validate_document(doc)
    # 不算 error —— 结构合法，只是需要人工定优先级
    assert result.ok
    assert result.status == "conflict"
    c = _conflicts(result)
    assert len(c) == 1
    # detail 里是两组关键词，不代定优先级
    assert set(c[0].detail["groups"].keys()) == {"speed", "safety"}
    assert "人工" in c[0].message


def test_no_conflict_passes():
    doc = make_task([make_module("m01", acceptance=["按日产出统计报表"])])
    result = validate_document(doc)
    assert result.status == "pass"
    assert _conflicts(result) == []


def test_conflict_check_can_be_disabled():
    doc = make_task(
        [make_module("m01", acceptance=["响应要快", "安全第一"])],
        integration={"check": {"acceptance_conflict": False}},
    )
    result = validate_document(doc)
    assert result.status == "pass"
    assert _conflicts(result) == []


def test_custom_groups_override():
    from fw_protocol.conflicts import DEFAULT_CONFLICT_GROUPS
    custom = {"cheap": ["省钱", "便宜"], "premium": ["豪华", "贵"]}
    doc = make_task([make_module("m01", acceptance=["又要省钱又要豪华"])])
    # 默认组不命中 → pass
    assert validate_document(doc).status == "pass"
    # 自定义组命中 → conflict
    result = validate_document(doc, groups=custom)
    assert result.status == "conflict"
    c = _conflicts(result)
    assert set(c[0].detail["groups"].keys()) == {"cheap", "premium"}
