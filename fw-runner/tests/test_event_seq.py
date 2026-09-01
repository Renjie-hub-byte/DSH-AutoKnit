"""事件 seq 完整性：dispatch.jsonl 单调递增、run_id 稳定、resume 续号。"""
from __future__ import annotations

import json
from pathlib import Path

from fw_runner.runner import run


def _events(root: Path):
    out = []
    with open(root / "总日志" / "dispatch.jsonl", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def test_event_seq_monotonic_and_run_id(single_root, harness):
    result = run(single_root, executor_driver=harness.make_executor(),
                 auditor_driver=harness.make_auditor())
    events = [e for e in _events(single_root) if "seq" in e]   # 过滤 scaffold 初始化事件
    assert events[0]["event"] == "run.start"
    assert events[-1]["event"] == "integration.check"   # 集成钩子为最后事件（正确收尾）
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    assert all(e["run_id"] == result.run_id for e in events)
    assert result.seq_events == len(events) == seqs[-1]
    # 事件流覆盖关键动作
    kinds = {e["event"] for e in events}
    assert {"run.start", "module.dispatch", "executor.round.start", "executor.round.done",
            "auditor.round.start", "auditor.round", "module.done"} <= kinds


def test_event_seq_continues_after_resume(resume_root, harness):
    """resume 后 seq 从快照 last_seq 续号，不重复。"""
    from fw_runner.checkpoint import read_snapshot

    def executor(ctx):
        from fw_runner.model import DriverOutcome
        from fw_runner.review import append_done
        append_done(ctx.module.review_path, f"exec {ctx.round_no}")
        (ctx.module.dir / "src" / f"o{ctx.round_no}.txt").write_text("x\n", encoding="utf-8")
        if ctx.module.id == "m03" and ctx.round_no == 1:
            return DriverOutcome(status="interrupted", reason="中断演示")
        return DriverOutcome(status="ok", substance=True)

    def auditor(ctx):
        from fw_runner.model import DriverOutcome
        return DriverOutcome(status="ok", verdict="pass", evidence_level="L2", root="", confidence=0.9)

    from fw_runner.drivers import InlineAgentDriver
    r1 = run(resume_root, executor_driver=InlineAgentDriver(executor),
             auditor_driver=InlineAgentDriver(auditor))
    assert r1.status == "interrupted"
    snap = read_snapshot(resume_root)
    assert snap["last_seq"] == r1.seq_events

    r2 = run(resume_root, executor_driver=InlineAgentDriver(executor),
             auditor_driver=InlineAgentDriver(auditor), resume=True)
    assert r2.status == "complete"
    events = [e for e in _events(resume_root) if "seq" in e]
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    # run.resume 首事件 seq == 快照 last_seq + 1
    rseqs = [e for e in events if e["event"] == "run.resume"]
    assert rseqs and rseqs[0]["seq"] == snap["last_seq"] + 1


def test_fresh_rerun_rotates_old_dispatch(indep4_root, harness):
    """同一任务根从零 run 两次（非 resume）：旧 run_id 事件流归档，
    新 run 的 dispatch.jsonl seq 从 1 重新开始且域内严格单调（事件完整性按 run 域保证）。"""
    from fw_runner.events import existing_run_ids
    from fw_runner.runner import run

    exec_driver = harness.make_executor()
    aud_driver = harness.make_auditor()

    r1 = run(indep4_root, executor_driver=exec_driver, auditor_driver=aud_driver)
    assert r1.status == "complete"

    # 第二次从零 run（新 run_id，重新生成完整执行）
    r2 = run(indep4_root, executor_driver=exec_driver, auditor_driver=aud_driver)
    assert r2.status == "complete"
    assert r2.run_id != r1.run_id

    # 旧事件流已归档（保留审计轨迹），活动 dispatch.jsonl 只含新 run 的干净 seq 域
    archives = list((indep4_root / "总日志").glob("dispatch-archive-*.jsonl"))
    assert len(archives) == 1, archives
    assert existing_run_ids(archives[0]) == [r1.run_id]
    assert existing_run_ids(indep4_root / "总日志" / "dispatch.jsonl") == [r2.run_id]

    # 活动日志 seq 从 1 开始且严格单调不重复
    evs = [e for e in _events(indep4_root) if "seq" in e]
    seqs = [e["seq"] for e in evs]
    assert seqs[0] == 1
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    assert all(e["run_id"] == r2.run_id for e in evs)
