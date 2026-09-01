"""需求1 验收1：含环的依赖图 → 报错并指出环路径。"""
from conftest import make_module, make_task

from fw_protocol import validate_document


def _find(result, code):
    return [i for i in result.errors if i.code == code]


def test_cycle_reports_cycle_path(cycle_task):
    result = validate_document(cycle_task)
    assert result.status == "error"
    assert not result.ok
    cycles = _find(result, "dep_cycle")
    assert len(cycles) == 1
    detail = cycles[0].detail
    path = detail["cycle"]  # 例如 ['m01','m03','m02','m01']
    assert path[0] == path[-1]          # 闭环
    assert len(path) == 4               # 3 个节点 + 回到起点
    assert set(path[:-1]) == {"m01", "m02", "m03"}
    # 环路径出现在人类可读消息里
    assert "→" in cycles[0].message


def test_self_dependency_is_cycle():
    doc = make_task([make_module("m01", deps=["m01"])])
    result = validate_document(doc)
    cycles = _find(result, "dep_cycle")
    assert len(cycles) == 1
    assert cycles[0].detail["cycle"] == ["m01", "m01"]


def test_chain_no_cycle_passes():
    # 依赖链 A→D（D 依赖 A）不是环
    doc = make_task([
        make_module("m01"),
        make_module("m02", deps=["m01"]),
        make_module("m03", deps=["m02"]),
    ])
    result = validate_document(doc)
    assert result.ok
    assert _find(result, "dep_cycle") == []


def test_deeper_cycle_detected():
    doc = make_task([
        make_module("m01", deps=["m02"]),
        make_module("m02", deps=["m03"]),
        make_module("m03", deps=["m04"]),
        make_module("m04", deps=["m01"]),   # 长环: m01→m02→m03→m04→m01
    ])
    result = validate_document(doc)
    cycles = _find(result, "dep_cycle")
    assert len(cycles) == 1
    path = cycles[0].detail["cycle"]
    assert path[0] == path[-1] == "m01"
    assert set(path[:-1]) == {"m01", "m02", "m03", "m04"}


def test_unknown_dependency_is_error():
    doc = make_task([make_module("m01"), make_module("m02", deps=["m99"])])
    result = validate_document(doc)
    issues = _find(result, "dep_unknown_module")
    assert len(issues) == 1
    assert issues[0].detail["unknown_dependency"] == "m99"


def test_duplicate_module_id_is_error():
    doc = make_task([make_module("m01"), make_module("m01")])
    result = validate_document(doc)
    assert _find(result, "module_id_duplicate")
