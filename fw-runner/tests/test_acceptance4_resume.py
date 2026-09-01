"""需求4 验收 4：中断后 --resume-from-checkpoint → 从快照接续不重跑。

机器可复现断言：
- 第一次运行：m03 第 1 轮报告 interrupted → 快照 status=interrupted，m01/m02 done
- 第二次 --resume-from-checkpoint：m01/m02 的 executor/auditor **不再被调用**，
  m03 续跑第 2 轮完成；completed == [m01,m02,m03]
- run_id 延续同一，事件 seq 从快照 last_seq 续号
- 快照每模块完成即写（checkpoint_every=1）
"""
from __future__ import annotations

import json
from pathlib import Path

from fw_runner.checkpoint import read_snapshot
from fw_runner.model import DriverOutcome
from fw_runner.runner import RunInterrupted, run


class _ResumeHarness:
    def __init__(self):
        import threading
        self.lock = threading.Lock()
        self.exec_calls: list[tuple[str, int]] = []
        self.audit_calls: list[str] = []
        self.interrupted = False

    def build(self):
        from fw_runner.drivers import InlineAgentDriver

        def executor(ctx):
            mid, rnd, mdir = ctx.module.id, ctx.round_no, ctx.module.dir
            with self.lock:
                self.exec_calls.append((mid, rnd))
            from fw_runner.review import append_done
            append_done(mdir / "REVIEW.md", f"{mid} round {rnd}")
            (mdir / "src" / f"out-{rnd}.txt").write_text("x\n", encoding="utf-8")
            if mid == "m03" and rnd == 1 and not self.interrupted:
                self.interrupted = True
                return DriverOutcome(status="interrupted",
                                     reason="测试模拟中断（m03 第 1 轮）")
            return DriverOutcome(status="ok", substance=True, tokens=0)

        def auditor(ctx):
            with self.lock:
                self.audit_calls.append(ctx.module.id)
            return DriverOutcome(status="ok", verdict="pass", evidence_level="L2", root="", confidence=0.9,
                                 reason="通过")

        return InlineAgentDriver(executor), InlineAgentDriver(auditor)


def test_interrupt_then_resume_no_rerun(resume_root):
    h = _ResumeHarness()
    exec_driver, aud_driver = h.build()

    # ---- 第一次运行：中断 ----
    r1 = run(resume_root, executor_driver=exec_driver, auditor_driver=aud_driver)
    assert r1.status == "interrupted", r1.to_dict()
    assert r1.exit_reason == "interrupted"
    assert sorted(r1.completed) == ["m01", "m02"]

    snap1 = read_snapshot(resume_root)
    assert snap1["status"] == "interrupted"
    assert snap1["modules"]["m01"] == "done"
    assert snap1["modules"]["m02"] == "done"
    assert snap1["modules"]["m03"] in ("running", "pending")
    assert snap1["run_id"] == r1.run_id
    last_seq = snap1["last_seq"]

    # 快照每模块完成即写（checkpoint_every=1）→ 快照里 m01/m02 的轮次已持久化
    assert snap1["per_module"]["m01"]["executor_round"] == 1
    assert snap1["per_module"]["m02"]["executor_round"] == 1

    # ---- 第二次运行：resume ----
    r2 = run(resume_root, executor_driver=exec_driver, auditor_driver=aud_driver, resume=True)
    assert r2.status == "complete", r2.to_dict()
    assert sorted(r2.completed) == ["m01", "m02", "m03"]   # 并行批次内完成顺序不定，按集合断言
    assert r2.run_id == r1.run_id, "resume 应延续同一 run_id（事件流连续）"
    assert r2.seq_events > last_seq, "resume 后事件 seq 应继续递增"

    # ---- 不重跑断言：executor 调用记录（多重集）----
    # m01/m02 各 1 次（首次）；m03 2 次（第 1 轮中断 + resume 第 2 轮）；无第二次 m01/m02
    assert sorted(h.exec_calls) == [("m01", 1), ("m02", 1), ("m03", 1), ("m03", 2)], h.exec_calls
    # m03 resume 轮是第 2 轮（executor_round 从快照续接，不重置）
    assert sorted(c for c in h.exec_calls if c[0] == "m03") == [("m03", 1), ("m03", 2)]
    assert sorted(h.audit_calls) == ["m01", "m02", "m03"], "m01/m02 auditor 不应被再次调用"

    # ---- 事件 seq 单调递增（跨 resume 无重复）----
    events = [e for e in _read_events(resume_root) if "seq" in e]
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs), "seq 必须严格单调不重复"
    # resume 首事件 seq 紧接快照 last_seq
    resume_first_seq = None
    for e in events:
        if e["event"] == "run.resume":
            resume_first_seq = e["seq"]
            break
    assert resume_first_seq is not None
    assert resume_first_seq == last_seq + 1, (resume_first_seq, last_seq)


def _read_events(root: Path):
    out = []
    with open(root / "总日志" / "dispatch.jsonl", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out
