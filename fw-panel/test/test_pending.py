"""test_pending —— 读 human_pending：needs_human + human_pending 文件。"""

import pytest

from autoknit_panel.snapshot import load_snapshot, parse_snapshot
from autoknit_panel.pending import read_human_pending


def test_pending_from_file():
    import conftest
    pend = read_human_pending(None, conftest.human_pending_path())
    assert len(pend) == 2
    assert pend[0]["text"] == "模块 m01 存在同名单冲突，请选择处理方式"
    assert pend[0]["choices"] == ["A", "B", "C", "D"]
    assert pend[0]["module"] == "m01"
    assert pend[1]["module"] == "m02"


def test_pending_from_snapshot_needs_human():
    snap = parse_snapshot({
        "needs_human": [
            {"text": "m02 接口签名有出入，请选择", "choices": ["A", "B"], "module": "m02"}
        ]
    })
    pend = read_human_pending(snap, None)
    assert len(pend) == 1
    assert pend[0]["module"] == "m02"


def test_pending_merges_snapshot_and_file():
    import conftest
    snap = parse_snapshot({"needs_human": ["快照里的待决策"]})
    pend = read_human_pending(snap, conftest.human_pending_path())
    assert len(pend) == 3
    assert any(p["text"] == "快照里的待决策" for p in pend)


def test_pending_missing_file_returns_empty(tmp_path):
    pend = read_human_pending(None, str(tmp_path / "missing.json"))
    assert pend == []
