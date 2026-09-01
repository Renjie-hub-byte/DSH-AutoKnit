"""需求1 验收2：两模块同接口前缀+方法 → 报错（指出双方模块）。"""
from conftest import make_module, make_task

from fw_protocol import validate_document


def _dups(result):
    return [i for i in result.errors if i.code == "interface_duplicate"]


def test_two_modules_same_prefix_method_reports_both():
    doc = make_task([
        make_module("m01", interfaces=[{"path": "/api/order/*", "method": ["POST"]}]),
        make_module("m02", interfaces=[{"path": "/api/order/*", "method": ["POST"]}]),
    ])
    result = validate_document(doc)
    assert result.status == "error"
    dups = _dups(result)
    assert len(dups) == 1
    assert set(dups[0].detail["modules"]) == {"m01", "m02"}
    assert dups[0].detail["path"] == "/api/order/*"
    assert dups[0].detail["shared_methods"] == ["POST"]
    # 人类可读消息包含两个模块 id
    assert "m01" in dups[0].message and "m02" in dups[0].message


def test_same_prefix_different_methods_is_ok():
    doc = make_task([
        make_module("m01", interfaces=[{"path": "/api/order/*", "method": ["POST"]}]),
        make_module("m02", interfaces=[{"path": "/api/order/*", "method": ["GET"]}]),
    ])
    result = validate_document(doc)
    assert result.ok
    assert _dups(result) == []


def test_method_case_insensitive():
    doc = make_task([
        make_module("m01", interfaces=[{"path": "/api/order/*", "method": "post"}]),
        make_module("m02", interfaces=[{"path": "/api/order/*", "method": ["POST"]}]),
    ])
    result = validate_document(doc)
    assert len(_dups(result)) == 1


def test_same_module_duplicate_flagged():
    doc = make_task([
        make_module("m01", interfaces=[
            {"path": "/api/order/*", "method": ["GET"]},
            {"path": "/api/order/*", "method": ["GET", "POST"]},
        ]),
    ])
    result = validate_document(doc)
    dups = _dups(result)
    assert len(dups) == 1
    assert dups[0].detail["modules"] == ["m01", "m01"]


def test_multiple_distinct_duplicates_all_reported():
    doc = make_task([
        make_module("m01", interfaces=[{"path": "/api/a", "method": ["GET"]},
                                       {"path": "/api/b", "method": ["POST"]}]),
        make_module("m02", interfaces=[{"path": "/api/a", "method": ["GET"]}]),
        make_module("m03", interfaces=[{"path": "/api/b", "method": ["POST"]}]),
    ])
    result = validate_document(doc)
    assert len(_dups(result)) == 2
