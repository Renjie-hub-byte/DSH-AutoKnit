"""test_control —— 一键暂停/继续包装单测。

覆盖本轮 remaining scope 的暂停/继续接口：
  * request_pause 写入 pause 信号文件（暂停到当前节点结束，不打断在跑模块）
  * request_resume 删除信号文件解除暂停
  * is_paused / pause_state / node_may_start / pause_boundary 状态查询
  * 路径解析（显式 path > 环境变量 FW_PAUSE_PATH > task_root/总日志/pause.json）
"""

import json
import os

import pytest

from autoknit_panel import control as c


# ---------------------------------------------------------------------------
# 路径解析
# ---------------------------------------------------------------------------
def test_resolve_pause_path_default_under_task_root(tmp_path):
    resolved = c.resolve_pause_path(task_root=str(tmp_path))
    assert resolved == os.path.abspath(os.path.join(str(tmp_path), "总日志", "pause.json"))


def test_resolve_pause_path_env_var(tmp_path, monkeypatch):
    expected = str(tmp_path / "from_env.json")
    monkeypatch.setenv("FW_PAUSE_PATH", expected)
    assert c.resolve_pause_path() == os.path.abspath(expected)


def test_resolve_pause_path_explicit_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("FW_PAUSE_PATH", str(tmp_path / "env.json"))
    assert c.resolve_pause_path(str(tmp_path / "explicit.json")) == os.path.abspath(
        str(tmp_path / "explicit.json")
    )


# ---------------------------------------------------------------------------
# 暂停
# ---------------------------------------------------------------------------
def test_request_pause_writes_signal(tmp_path):
    path = tmp_path / "pause.json"
    result = c.request_pause(reason="人工审核", path=str(path))
    assert result["paused"] is True
    assert result["path"] == str(path)
    assert result["reason"] == "人工审核"
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["state"] == "paused"
    assert data["paused"] is True
    assert data["reason"] == "人工审核"


def test_request_pause_creates_parent_dir(tmp_path):
    nested = tmp_path / "总日志" / "pause.json"
    c.request_pause(path=str(nested))
    assert nested.exists()


# ---------------------------------------------------------------------------
# 继续
# ---------------------------------------------------------------------------
def test_request_resume_removes_signal(tmp_path):
    path = tmp_path / "pause.json"
    c.request_pause(path=str(path))
    result = c.request_resume(path=str(path))
    assert result["resumed"] is True
    assert result["paused"] is False
    assert not path.exists()


def test_request_resume_idempotent_when_no_signal(tmp_path):
    path = tmp_path / "pause.json"
    result = c.request_resume(path=str(path))
    assert result["resumed"] is True
    assert not path.exists()


# ---------------------------------------------------------------------------
# 状态查询
# ---------------------------------------------------------------------------
def test_is_paused_false_when_no_signal(tmp_path):
    assert c.is_paused(str(tmp_path / "pause.json")) is False


def test_is_paused_true_after_pause(tmp_path):
    path = tmp_path / "pause.json"
    c.request_pause(path=str(path))
    assert c.is_paused(str(path)) is True


def test_is_paused_false_after_resume(tmp_path):
    path = tmp_path / "pause.json"
    c.request_pause(path=str(path))
    c.request_resume(path=str(path))
    assert c.is_paused(str(path)) is False


def test_pause_state_reports_reason(tmp_path):
    path = tmp_path / "pause.json"
    c.request_pause(reason="等审批", path=str(path))
    state = c.pause_state(str(path))
    assert state["paused"] is True
    assert state["state"] == "paused"
    assert state["reason"] == "等审批"
    assert state["path"] == str(path)


def test_pause_state_reports_resumed(tmp_path):
    path = tmp_path / "pause.json"
    c.request_resume(path=str(path))
    state = c.pause_state(str(path))
    assert state["paused"] is False
    assert state["state"] == "resumed"


def test_corrupt_signal_treated_as_paused(tmp_path):
    path = tmp_path / "pause.json"
    path.write_text("{ 半截 json", encoding="utf-8")
    assert c.is_paused(str(path)) is True


# ---------------------------------------------------------------------------
# 节点边界语义（暂停到当前节点结束，不打断在跑模块）
# ---------------------------------------------------------------------------
def test_node_may_start_false_while_paused(tmp_path):
    path = tmp_path / "pause.json"
    c.request_pause(path=str(path))
    assert c.node_may_start(path=str(path)) is False


def test_node_may_start_true_when_resumed(tmp_path):
    path = tmp_path / "pause.json"
    c.request_pause(path=str(path))
    c.request_resume(path=str(path))
    assert c.node_may_start(path=str(path)) is True


def test_node_may_start_accepts_explicit_flag():
    assert c.node_may_start(paused=False) is True
    assert c.node_may_start(paused=True) is False


def test_pause_boundary_block_fields(tmp_path):
    path = tmp_path / "pause.json"
    c.request_pause(reason="人工", path=str(path))
    block = c.pause_boundary(path=str(path))
    assert set(block) == {"may_start", "paused", "reason"}
    assert block["may_start"] is False
    assert block["paused"] is True
    assert block["reason"] == "人工"


def test_pause_boundary_may_start_when_resumed(tmp_path):
    path = tmp_path / "pause.json"
    c.request_resume(path=str(path))
    block = c.pause_boundary(path=str(path))
    assert block["may_start"] is True
    assert block["paused"] is False


def test_control_error_importable_and_subclass_of_valueerror():
    assert issubclass(c.ControlError, ValueError)
