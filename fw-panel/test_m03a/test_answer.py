"""test_answer —— 提交/回复落 human_answer.json 逻辑与阻塞即停（blocker）单测。

覆盖验收第 2、3 条：选 A/B/C/D 或文本写入 human_answer.json、触发 resume 接续、
阻塞即停（未回复不继续烧 token）。
"""

import json
import os

import pytest

from autoknit_panel import answer as a
from autoknit_panel import blocker as b
from autoknit_panel import decision as d


# ---------------------------------------------------------------------------
# choice 校验
# ---------------------------------------------------------------------------
def test_normalize_choice_lower_and_upper():
    assert a.normalize_choice("a") == "A"
    assert a.normalize_choice(" TEXT ") == "text"


@pytest.mark.parametrize("bad", ["", "E", "AB", 3, None, "x"])
def test_invalid_choice_raises(bad):
    with pytest.raises(a.AnswerError):
        a.normalize_choice(bad)


def test_text_choice_requires_nonempty_text(tmp_path):
    with pytest.raises(a.AnswerError):
        a.write_answer("text", text=None, path=str(tmp_path / "h.json"))
    with pytest.raises(a.AnswerError):
        a.write_answer("text", text="  ", path=str(tmp_path / "h.json"))


# ---------------------------------------------------------------------------
# 写盘（choice 落盘 / 文本落盘 / resume 触发）
# ---------------------------------------------------------------------------
def test_write_choice_answer(tmp_path):
    path = tmp_path / "human_answer.json"
    result = a.write_answer("B", path=str(path))
    assert result["choice"] == "B"
    assert result["resume_ready"] is True
    assert result["path"] == str(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {"choice": "B"}


def test_write_choice_with_text(tmp_path):
    path = tmp_path / "h.json"
    a.write_answer("C", text="选 C，并补充一句", path=str(path))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {"choice": "C", "text": "选 C，并补充一句"}


def test_write_text_reply(tmp_path):
    path = tmp_path / "h.json"
    a.submit_answer("text", text="直接给真人回复", path=str(path))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {"choice": "text", "text": "直接给真人回复"}


def test_submit_answer_is_alias_of_write(tmp_path):
    path = tmp_path / "h.json"
    a.submit_answer("D", path=str(path))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {"choice": "D"}


def test_write_creates_parent_dir(tmp_path):
    nested = tmp_path / "总日志" / "human_answer.json"
    a.write_answer("A", path=str(nested))
    assert nested.exists()


def test_read_answer_missing_returns_none(tmp_path):
    assert a.read_answer(str(tmp_path / "nope.json")) is None


def test_read_answer_after_write(tmp_path):
    path = tmp_path / "h.json"
    a.write_answer("A", path=str(path))
    assert a.read_answer(str(path)) == {"choice": "A"}


# ---------------------------------------------------------------------------
# resume 触发
# ---------------------------------------------------------------------------
def test_trigger_resume_ready_after_write(tmp_path):
    path = tmp_path / "h.json"
    a.write_answer("A", path=str(path))
    status = a.trigger_resume(str(path))
    assert status["resumed"] is True
    assert status["path"] == str(path)


def test_trigger_resume_not_ready_without_file(tmp_path):
    status = a.trigger_resume(str(tmp_path / "missing.json"))
    assert status["resumed"] is False


def test_trigger_resume_required_choice_mismatch(tmp_path):
    path = tmp_path / "h.json"
    a.write_answer("A", path=str(path))
    status = a.trigger_resume(str(path), required_choice="B")
    assert status["resumed"] is False


# ---------------------------------------------------------------------------
# 路径解析：契约 env_var / task_root / 显式
# ---------------------------------------------------------------------------
def test_resolve_answer_path_env_var(tmp_path, monkeypatch):
    expected = str(tmp_path / "from_env.json")
    monkeypatch.setenv("FW_HUMAN_ANSWER", expected)
    assert a.resolve_answer_path() == os.path.abspath(expected)


def test_resolve_answer_path_task_root(tmp_path):
    resolved = a.resolve_answer_path(task_root=str(tmp_path))
    assert resolved == os.path.abspath(os.path.join(str(tmp_path), "总日志", "human_answer.json"))


def test_resolve_answer_path_explicit_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("FW_HUMAN_ANSWER", str(tmp_path / "env.json"))
    assert a.resolve_answer_path(str(tmp_path / "explicit.json")) == os.path.abspath(
        str(tmp_path / "explicit.json")
    )


# ---------------------------------------------------------------------------
# blocker：阻塞即停（没回复不烧 token）
# ---------------------------------------------------------------------------
def test_blocked_when_pending_needs_human_and_no_answer():
    pending = [{"needs_human": True, "message": "外部信息请求"}]
    assert b.is_blocked(human_pending=pending, answer=None) is True


def test_not_blocked_when_answer_present():
    pending = [{"needs_human": True, "message": "外部信息请求"}]
    assert b.is_blocked(human_pending=pending, answer={"choice": "A"}) is False


def test_not_blocked_without_pending():
    assert b.is_blocked(human_pending=[]) is False


def test_resume_ready_complements_blocked():
    pending = [{"needs_human": True, "message": "x"}]
    assert b.resume_ready(human_pending=pending, answer=None) is False
    assert b.resume_ready(human_pending=pending, answer={"choice": "C"}) is True


def test_blocker_uses_decision_classify():
    events = [
        {"role": "auditor", "module": "m02", "outcome": "reject"},
        {"role": "auditor", "module": "m02", "outcome": "reject"},
    ]
    assert b.is_blocked(events=events, answer=None) is True
    assert b.is_blocked(events=events, answer={"choice": "B"}) is False


def test_blocked_until_answer_written_to_disk(tmp_path):
    path = tmp_path / "h.json"
    pending = [{"needs_human": True, "message": "x"}]
    # 未落盘 → 阻塞
    assert b.is_blocked(human_pending=pending, answer_path=str(path)) is True
    # 落盘后 → 解除阻塞
    a.write_answer("A", path=str(path))
    assert b.is_blocked(human_pending=pending, answer_path=str(path)) is False


def test_requires_human_default_true_for_string():
    assert b.requires_human("某个待决策文本") is True


def test_requires_human_false_for_pending_decision_with_needs_human_false():
    pd = d.PendingDecision(kind="external_request", message="x", needs_human=False)
    assert b.requires_human(pd) is False
