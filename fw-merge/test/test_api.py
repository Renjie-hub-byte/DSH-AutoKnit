"""Tests for fw_merge.api (dsh.merge.conflicts / dsh.merge.skeleton get)."""

import pytest

from fw_merge import api


def test_get_conflicts_shape(sample_task):
    payload = api.get("dsh.merge.conflicts", sample_task)
    assert isinstance(payload, list)
    assert len(payload) >= 2  # same_name + naming_conflict
    for item in payload:
        assert set(item.keys()) == {"kind", "module_refs", "description", "needs_human"}
        assert item["kind"] in (
            "same_name",
            "naming_conflict",
            "signature_mismatch",
            "semantic_merge",
        )
        assert isinstance(item["module_refs"], list)
        assert isinstance(item["description"], str)
        assert isinstance(item["needs_human"], bool)
    kinds = {i["kind"] for i in payload}
    assert "same_name" in kinds
    assert "naming_conflict" in kinds


def test_get_skeleton_shape(sample_task):
    payload = api.get("dsh.merge.skeleton", sample_task)
    assert isinstance(payload, list)
    for item in payload:
        assert set(item.keys()) == {"target_path", "source_module", "kind"}
        assert item["kind"] in ("dir", "file")
    assert any(i["kind"] == "dir" for i in payload)
    assert any(i["kind"] == "file" for i in payload)


def test_get_no_conflicts_empty_list(tmp_path):
    from helpers import _write

    root = str(tmp_path / "clean")
    _write(f"{root}/modules/mx/src/a.py", "x=1\n")
    _write(f"{root}/modules/mx/interface.json", '{"name": "catalog"}\n')
    assert api.get("dsh.merge.conflicts", root) == []


def test_get_unsupported_api_raises(sample_task):
    with pytest.raises(KeyError):
        api.get("dsh.merge.nope", sample_task)
