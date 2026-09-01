"""test_builder —— 端到端：build_from_paths 从任务根存储读出面板状态。"""

import os
import shutil
import pytest

from autoknit_panel.builder import build_from_paths


@pytest.fixture
def task_root(tmp_path):
    """按契约布局搭一个临时任务根：{root}/总日志/{快照.json, dispatch.jsonl, human_pending.json}。"""
    import conftest
    store = tmp_path / "总日志"
    store.mkdir()
    shutil.copy(conftest.snapshot_path(), store / "快照.json")
    shutil.copy(conftest.dispatch_path(), store / "dispatch.jsonl")
    shutil.copy(conftest.human_pending_path(), store / "human_pending.json")
    return str(tmp_path)


def test_build_from_paths_end_to_end(task_root):
    state = build_from_paths(task_root=task_root)
    assert state["stage"] == "audit"
    assert "executor" in state["roles"] and "auditor" in state["roles"]
    cons = state["consumption"]
    assert cons["token_input"] > 0
    assert isinstance(cons["cache_hit"], bool)
    assert len(state["pending"]) == 2
    assert state["progress"]["total"] == 3
    assert state["progress"]["done"] == 2


def test_build_from_paths_missing_snapshot_defaults(task_root):
    # 移除快照 → require_snapshot=False 时返回空状态骨架
    os.remove(os.path.join(task_root, "总日志", "快照.json"))
    state = build_from_paths(task_root=task_root, require_snapshot=False)
    assert state["stage"] == "idle"
    assert state["roles"] == []
    assert state["consumption"]["token_input"] == 0


def test_build_from_paths_missing_snapshot_raises(task_root):
    os.remove(os.path.join(task_root, "总日志", "快照.json"))
    with pytest.raises(FileNotFoundError):
        build_from_paths(task_root=task_root, require_snapshot=True)
