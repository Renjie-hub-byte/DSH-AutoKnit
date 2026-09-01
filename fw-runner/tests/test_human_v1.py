"""v1.0 H：人机通道最小实现（H1–H2）。

- H1  HUMAN 触发 → 打印「模块 mXX 需要人工决策」+ 四个预定义选项
- H2  stdin 读取真人回复 → 写入 human_answer.json → resume 继续

验证方式：代码级 + 单测（不跑真 dsh 端到端，约束 3；split agent 以 mock 驱动）。
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from fw_runner import runner as runner_mod
from fw_runner import human as human_mod
from fw_runner.context import load_task_context
from fw_runner.drivers import InlineAgentDriver
from fw_runner.events import EventLog
from fw_runner.human import (
    HUMAN_OPTIONS,
    apply_human_answers,
    human_answer_path,
    human_escalate,
    interactive_human_enabled,
    prompt_text,
    read_human_answer,
    read_human_input,
    write_human_answer,
)
from fw_runner.model import DriverOutcome, RunState
from fw_runner.runner import run
from fw_runner.split import SplitJSONError

def _block_drivers():
    """executor 有产出；auditor 恒定 block → 走完升级链 → 回人。"""
    from fw_runner.review import append_done

    def executor(ctx):
        append_done(ctx.module.review_path, f"exec {ctx.round_no} ({ctx.executor_id})")
        return DriverOutcome(status="ok", substance=True, tokens=0)

    def auditor(ctx):
        return DriverOutcome(status="ok", verdict="block", root="self", confidence=0.4,
                             reason="演示持续 block", blocker="b")

    return InlineAgentDriver(executor), InlineAgentDriver(auditor)


# ---------- H1：提示与选项 ----------

def test_prompt_text_contains_title_and_options():
    text = prompt_text("m01", "上限回人")
    assert "模块 m01 需要人工决策" in text
    assert "原因: 上限回人" in text
    assert set(HUMAN_OPTIONS.keys()) == {"A", "B", "C", "D"}
    for code, label in HUMAN_OPTIONS.items():
        assert f"[{code}]" in text, code
        assert label in text, label


def test_interactive_human_enabled(monkeypatch):
    # 未显式设置 + pytest 环境 → 非交互（不阻塞，保护 headless/CI/测试）
    assert interactive_human_enabled(env={}) is False
    # 显式开关优先
    assert interactive_human_enabled(env={"FW_HUMAN_INTERACTIVE": "1"}) is True
    assert interactive_human_enabled(env={"FW_HUMAN_INTERACTIVE": "true"}) is True
    assert interactive_human_enabled(env={"FW_HUMAN_INTERACTIVE": "on"}) is True
    assert interactive_human_enabled(env={"FW_HUMAN_INTERACTIVE": "0"}) is False
    assert interactive_human_enabled(env={"FW_HUMAN_INTERACTIVE": "off"}) is False


# ---------- H2：stdin 读取 ----------

def test_read_human_input_parses_codes():
    def fake(inp):
        return lambda _prompt: inp

    assert read_human_input("m01", input_fn=fake("A")) == ("A", "")
    assert read_human_input("m01", input_fn=fake("B")) == ("B", "")
    assert read_human_input("m01", input_fn=fake("C")) == ("C", "")
    assert read_human_input("m01", input_fn=fake("D: 调整交付物范围")) == ("D", "调整交付物范围")
    assert read_human_input("m01", input_fn=fake("d：改范围")) == ("D", "改范围")
    # 无效/空输入兜底 D（原文保留）
    code, text = read_human_input("m01", input_fn=fake("x"))
    assert code == "D" and text == "x"
    code, text = read_human_input("m01", input_fn=fake(""))
    assert code == "D"


# ---------- H2：human_answer.json 写入/读取 ----------

def test_write_read_human_answer_roundtrip(single_root):
    ctx = load_task_context(single_root)
    path = write_human_answer(ctx, "m01", "D", "自定义说明", root="self", reason="上限回人")
    assert path == human_answer_path(ctx)
    assert path.is_file()
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["answers"]["m01"]["code"] == "D"
    assert doc["answers"]["m01"]["text"] == "自定义说明"
    assert read_human_answer(ctx, "m01")["code"] == "D"
    # 幂等合并：同模块覆盖、异模块保留
    write_human_answer(ctx, "m01", "A", "", root="self", reason="放弃")
    write_human_answer(ctx, "m02", "B", "改方案", root="self", reason="block")
    assert read_human_answer(ctx, "m01")["code"] == "A"
    assert read_human_answer(ctx, "m02")["code"] == "B"
    assert set(read_human_answer(ctx).keys()) == {"m01", "m02"}
    assert read_human_answer(ctx, "no-such") == {}


# ---------- H1+H2：human_escalate 总入口 ----------

def _state_single(mid="m01"):
    state = RunState()
    state.run_id = "run-x"
    state.modules = {mid: "running"}
    state.failure_counts = {mid: 0}
    state.ensure(mid)
    return state


def test_human_escalate_non_interactive_prints_no_write(single_root, tmp_path, capsys):
    ctx = load_task_context(single_root)
    state = _state_single()
    events = EventLog(tmp_path / "dispatch.jsonl", "run-x")
    with mock.patch.object(human_mod, "interactive_human_enabled", return_value=False):
        out = human_escalate(ctx, state, "m01", events, root="self", reason="上限回人")
    assert out == "human"
    assert state.modules["m01"] == "needs_human"
    assert state.needs_human == ["m01"]
    # 非交互模式写答案模板文件（code='?'），供真人编辑后 --resume
    assert human_answer_path(ctx).is_file()
    ans = read_human_answer(ctx, "m01")
    assert ans["code"] == "?"
    assert "模块 m01 需要人工决策" in capsys.readouterr().out
    evt = [e for e in events.read_all() if e["event"] == "module.needs_human"]
    assert len(evt) == 1


def test_human_escalate_interactive_writes_answer(single_root, tmp_path):
    ctx = load_task_context(single_root)
    state = _state_single()
    events = EventLog(tmp_path / "dispatch.jsonl", "run-x")
    with mock.patch.object(human_mod, "interactive_human_enabled", return_value=True), \
         mock.patch.object(human_mod, "read_human_input", return_value=("A", "不要了")):
        out = human_escalate(ctx, state, "m01", events, root="self", reason="上限回人")
    assert out == "human"
    assert human_answer_path(ctx).is_file()
    assert read_human_answer(ctx, "m01")["code"] == "A"
    assert read_human_answer(ctx, "m01")["text"] == "不要了"


# ---------- H2：apply_human_answers（resume 收敛） ----------

def test_apply_human_answers_no_file_ok(single_root, tmp_path):
    ctx = load_task_context(single_root)
    state = _state_single()
    events = EventLog(tmp_path / "dispatch.jsonl", "run-x")
    assert apply_human_answers(ctx, state, events) == "ok"
    assert state.modules["m01"] == "running"   # 无答案文件，不动状态


def test_apply_human_answers_abandon(single_root, tmp_path):
    ctx = load_task_context(single_root)
    state = _state_single()
    state.modules["m01"] = "needs_human"
    state.needs_human = ["m01"]
    events = EventLog(tmp_path / "dispatch.jsonl", "run-x")
    write_human_answer(ctx, "m01", "A", "放弃", root="self", reason="上限回人")
    assert apply_human_answers(ctx, state, events) == "ok"
    assert state.modules["m01"] == "done"          # 放弃=按完成跳过
    assert state.needs_human == []
    assert "m01" in state.completed_order
    assert "（人工放弃）" in state.ensure("m01").reason
    evt = [e for e in events.read_all() if e["event"] == "module.human_abandoned"]
    assert len(evt) == 1


def test_apply_human_answers_rerun(single_root, tmp_path):
    ctx = load_task_context(single_root)
    state = _state_single()
    state.modules["m01"] = "needs_human"
    state.needs_human = ["m01"]
    st = state.ensure("m01")
    st.executor_round = 4
    st.auditor_round = 5
    st.executor_switches = 2
    st.block_count = 2
    st.block_total = 6
    st.model_tier = 1
    st.last_verdict = "block"
    events = EventLog(tmp_path / "dispatch.jsonl", "run-x")
    write_human_answer(ctx, "m01", "B", "已改方案", root="self", reason="block")
    assert apply_human_answers(ctx, state, events) == "ok"
    assert state.modules["m01"] == "pending"       # 改方案后重新执行
    assert state.needs_human == []
    assert st.executor_round == 0 and st.auditor_round == 0
    assert st.executor_switches == 0 and st.block_count == 0
    assert st.block_total == 0 and st.model_tier == 0
    assert st.last_verdict == ""
    evt = [e for e in events.read_all() if e["event"] == "module.human_rerun"]
    assert len(evt) == 1
    assert evt[0]["detail"]["text"] == "已改方案"


def test_apply_human_answers_custom_is_rerun(single_root, tmp_path):
    ctx = load_task_context(single_root)
    state = _state_single()
    state.modules["m01"] = "needs_human"
    state.needs_human = ["m01"]
    events = EventLog(tmp_path / "dispatch.jsonl", "run-x")
    write_human_answer(ctx, "m01", "D", "自定义重做", root="self", reason="上限回人")
    assert apply_human_answers(ctx, state, events) == "ok"
    assert state.modules["m01"] == "pending"
    evt = [e for e in events.read_all() if e["event"] == "module.human_rerun"]
    assert evt and evt[0]["detail"]["code"] == "D"


def test_apply_human_answers_pause(single_root, tmp_path):
    ctx = load_task_context(single_root)
    state = _state_single()
    state.modules["m01"] = "needs_human"
    state.needs_human = ["m01"]
    events = EventLog(tmp_path / "dispatch.jsonl", "run-x")
    write_human_answer(ctx, "m01", "C", "暂停", root="self", reason="稍后处理")
    assert apply_human_answers(ctx, state, events) == "paused"
    assert state.modules["m01"] == "needs_human"   # 暂停：保持 needs_human
    assert state.needs_human == ["m01"]


# ---------- H2：端到端 run → HUMAN → human_answer → resume 继续 ----------

def test_run_human_then_resume_abandon_completes(single_root):
    """H2 端到端：模块回人 → 真人选 [A]放弃 → resume 后跳过该模块，任务完成。"""
    exec_d, aud_d = _block_drivers()
    with mock.patch.object(runner_mod, "call_split_agent",
                           side_effect=SplitJSONError("cannot_split: 演示拆分失败")):
        r1 = run(single_root, executor_driver=exec_d, auditor_driver=aud_d)
    assert r1.status == "needs_human", r1.to_dict()
    assert r1.needs_human == ["m01"]

    ctx = load_task_context(single_root)
    write_human_answer(ctx, "m01", "A", "放弃", root="self", reason="block")

    with mock.patch.object(runner_mod, "call_split_agent",
                           side_effect=SplitJSONError("cannot_split")):
        r2 = run(single_root, resume=True, executor_driver=exec_d, auditor_driver=aud_d)
    assert r2.status == "complete", r2.to_dict()
    assert r2.needs_human == []
    assert "m01" in r2.completed
    # resume 事件流里记录了人工放弃
    dispatch = single_root / "总日志" / "dispatch.jsonl"
    evts = [json.loads(ln) for ln in dispatch.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert any(e["event"] == "module.human_abandoned" for e in evts)


def test_run_human_then_resume_rerun_after_plan_change(single_root):
    """H2 端到端：模块回人 → 真人选 [B]改方案 → resume 重新执行，auditor 改判 pass 后完成。"""
    exec_d, aud_d = _block_drivers()
    with mock.patch.object(runner_mod, "call_split_agent",
                           side_effect=SplitJSONError("cannot_split")):
        r1 = run(single_root, executor_driver=exec_d, auditor_driver=aud_d)
    assert r1.status == "needs_human"

    ctx = load_task_context(single_root)
    write_human_answer(ctx, "m01", "B", "已改方案", root="self", reason="block")

    def pass_auditor(ctx_):
        return DriverOutcome(status="ok", verdict="pass", evidence_level="L2", root="", confidence=0.9,
                             reason="改方案后通过")

    exec2, _ = _block_drivers()
    with mock.patch.object(runner_mod, "call_split_agent",
                           side_effect=SplitJSONError("cannot_split")):
        r2 = run(single_root, resume=True, executor_driver=exec2,
                 auditor_driver=InlineAgentDriver(pass_auditor))
    assert r2.status == "complete", r2.to_dict()
    assert r2.needs_human == []
    assert "m01" in r2.completed
    dispatch = single_root / "总日志" / "dispatch.jsonl"
    evts = [json.loads(ln) for ln in dispatch.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert any(e["event"] == "module.human_rerun" for e in evts)


def test_run_human_then_resume_pause_keeps_paused(single_root):
    """H2 端到端：真人选 [C]暂停任务 → resume 后任务保持 needs_human（human_paused）。"""
    exec_d, aud_d = _block_drivers()
    with mock.patch.object(runner_mod, "call_split_agent",
                           side_effect=SplitJSONError("cannot_split")):
        r1 = run(single_root, executor_driver=exec_d, auditor_driver=aud_d)
    assert r1.status == "needs_human"

    ctx = load_task_context(single_root)
    write_human_answer(ctx, "m01", "C", "暂停", root="self", reason="稍后处理")

    with mock.patch.object(runner_mod, "call_split_agent",
                           side_effect=SplitJSONError("cannot_split")):
        r2 = run(single_root, resume=True, executor_driver=exec_d, auditor_driver=aud_d)
    assert r2.status == "needs_human"
    assert r2.exit_reason == "human_paused"
    assert r2.needs_human == ["m01"]
