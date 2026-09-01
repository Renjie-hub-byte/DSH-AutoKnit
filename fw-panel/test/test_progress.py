"""test_progress —— 拼进度：build_progress 从快照+事件流得出模块进度。"""

import pytest

from autoknit_panel.snapshot import load_snapshot
from autoknit_panel.events import load_events
from autoknit_panel.progress import build_progress


@pytest.fixture
def snap():
    import conftest
    return load_snapshot(conftest.snapshot_path())


@pytest.fixture
def events():
    import conftest
    return load_events(conftest.dispatch_path())


def test_progress_counts(snap, events):
    prog = build_progress(snap, events)
    assert prog["total"] == 3
    assert prog["done"] == 2
    assert prog["percent"] == 66.7
    assert prog["status"] == "running"
    assert prog["note"] == "已 2 模块完成"
    assert prog["completed_order"] == ["m01", "m02"]


def test_progress_module_detail(snap, events):
    prog = build_progress(snap, events)
    m01 = prog["modules"]["m01"]
    assert m01["done"] is True
    assert m01["executor_round"] == 2
    assert m01["auditor_round"] == 2
    assert m01["tokens_used"] == 1200
    assert "executor" in m01["roles"] and "auditor" in m01["roles"]
    # m03 未完成
    assert prog["modules"]["m03"]["done"] is False


def test_progress_empty_snapshot():
    prog = build_progress(None, [])
    assert prog["total"] == 0
    assert prog["percent"] == 0.0
