"""证据等级门禁测试（BUG-002a，2026-08-25 杰哥拍板：不关沙箱，从验收/传输层解决）。

铁律：auditor 判 pass 但无实证（证据等级 L3 静态推演 / 未声明）→ 验收不成立，强制回人复核。
- L3 + pass → needs_human（门禁开）
- 未声明 + pass → 默认 L3 保守 → needs_human（门禁开）
- L1/L2 + pass → 正常 done（门禁开）
- 门禁关（audit_require_evidence=False）→ L3 + pass 走老行为 done（联调/降级通道）
"""
from __future__ import annotations

import json

from fw_runner.model import DriverOutcome, RunConfig
from fw_runner.runner import run
from fw_runner.drivers import InlineAgentDriver


def _mk_drivers(ev_level="L3", verdict="pass"):
    def executor(ctx):
        (ctx.module.dir / "src" / "out.txt").write_text("x\n", encoding="utf-8")
        return DriverOutcome(status="ok", substance=True)

    def auditor(ctx):
        return DriverOutcome(status="ok", verdict=verdict, root="", confidence=0.9,
                             evidence_level=ev_level,
                             evidence=["读了 src/out.txt", "跑了 test 1/1 通过"])

    return InlineAgentDriver(executor), InlineAgentDriver(auditor)


def _dispatch(task_root) -> str:
    p = task_root / "总日志" / "dispatch.jsonl"
    if not p.exists():
        return ""
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return json.dumps(out, ensure_ascii=False)


def test_l3_pass_gate_human(monkeypatch, single_root):
    """L3（静态推演）+ pass → 门禁拦截，回人（不静默 done）。"""
    monkeypatch.setattr(RunConfig, "audit_require_evidence", True)
    e, a = _mk_drivers(ev_level="L3")
    r = run(single_root, executor_driver=e, auditor_driver=a)
    assert r.status == "needs_human"
    assert r.completed == []
    assert "auditor.no_evidence_pass" in _dispatch(single_root)


def test_missing_level_pass_gate_human(monkeypatch, single_root):
    """未声明证据等级 + pass → 默认 L3 保守 → 回人。"""
    monkeypatch.setattr(RunConfig, "audit_require_evidence", True)
    e, a = _mk_drivers(ev_level="")
    r = run(single_root, executor_driver=e, auditor_driver=a)
    assert r.status == "needs_human"
    assert r.completed == []


def test_l1_pass_ok(monkeypatch, single_root):
    """L1（命令实跑）+ pass → 正常 done。"""
    monkeypatch.setattr(RunConfig, "audit_require_evidence", True)
    e, a = _mk_drivers(ev_level="L1")
    r = run(single_root, executor_driver=e, auditor_driver=a)
    assert r.status == "complete"
    assert r.completed == ["m01"]


def test_l2_pass_ok(monkeypatch, single_root):
    """L2（内容取证）+ pass → 正常 done。"""
    monkeypatch.setattr(RunConfig, "audit_require_evidence", True)
    e, a = _mk_drivers(ev_level="L2")
    r = run(single_root, executor_driver=e, auditor_driver=a)
    assert r.status == "complete"
    assert r.completed == ["m01"]


def test_gate_disabled_allows_l3(single_root):
    """门禁关（联调/降级通道）→ L3 + pass 走老行为 done。"""
    e, a = _mk_drivers(ev_level="L3")
    r = run(single_root, executor_driver=e, auditor_driver=a,
            overrides={"audit_require_evidence": False})
    assert r.status == "complete"
    assert r.completed == ["m01"]


def test_human_pending_passthrough(single_root):
    """v1.3：auditor 返回 human_pending（GUI 等人工验收项）→ 透传到 auditor.round 事件，
    且不阻塞 done（manual 项不进判定，代码层过了就是过了）。"""
    def executor(ctx):
        (ctx.module.dir / "src" / "out.txt").write_text("x\n", encoding="utf-8")
        return DriverOutcome(status="ok", substance=True)

    def auditor(ctx):
        return DriverOutcome(status="ok", verdict="pass", root="", confidence=0.9,
                             evidence_level="L2",
                             evidence=["跑了 test 1/1 通过"],
                             human_pending=["GUI 界面能正常展示", "真实浏览器里点按钮有反馈"])

    e, a = InlineAgentDriver(executor), InlineAgentDriver(auditor)
    r = run(single_root, executor_driver=e, auditor_driver=a)
    assert r.status == "complete"
    assert r.completed == ["m01"]
    events = _dispatch(single_root)
    assert "human_pending" in events
    assert "GUI 界面能正常展示" in events
