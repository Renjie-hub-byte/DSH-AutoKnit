"""需求5 验收③：人工加预算 resume 从快照继续不重跑。

复现路径（auditor 独立）：three_root（m01/m02 完成后预算 100% stop，m03 pending）→
fw_budget.add_budget(max_tokens 900→2000) → fw_budget.manage.resume（事件流重建闸门，
累计消耗灌回）→ complete；executor 调用序列 = [m01, m02, m03] 各 1 次（零重跑）。
"""
from __future__ import annotations

from fw_runner.checkpoint import read_snapshot

from fw_budget.manage import add_budget, resume, resume_advice
from fw_budget.meter import EventLogTokenMeter
from fw_budget.report import build_report

EXEC_TOKENS = {"m01": 300, "m02": 400, "m03": 300}
AUDIT_TOKENS = {"m01": 100, "m02": 100, "m03": 100}


def _run_first_phase(root, harness):
    """阶段1：m01(400) + m02(500) → 900/900=100% → stop；m03 pending。"""
    from fw_runner.runner import run as runner_run
    from fw_budget.gate_state import build_budget_gate
    return runner_run(root, executor_driver=harness.make_executor(),
                      auditor_driver=harness.make_auditor(),
                      budget_gate=build_budget_gate(root))


def test_resume_after_add_budget_no_rerun(three_root):
    from helpers import make_harness
    harness = make_harness(EXEC_TOKENS, AUDIT_TOKENS)

    # ---- 阶段1：预算 100% 硬停 ----
    result1 = _run_first_phase(three_root, harness)
    assert result1.status == "stopped", result1.to_dict()
    assert result1.tokens_used == 900
    assert result1.completed == ["m01", "m02"]
    assert harness.exec_calls == [{"mid": "m01", "round": 1}, {"mid": "m02", "round": 1}]
    assert harness.audit_calls == [{"mid": "m01", "round": 1}, {"mid": "m02", "round": 1}]

    report0 = build_report(three_root)
    assert report0.phase == "stopped"
    assert report0.unfinished == ["m03"]
    assert report0.tried == {"m01": 1, "m02": 1, "m03": 0}

    # resuming 前检查：当前预算会立即再停（提示加预算）
    advice = resume_advice(three_root)
    assert advice.would_stop_now is True
    assert "建议先" in advice.message

    # ---- 人工加预算 900 → 2000 ----
    upd = add_budget(three_root, 2000, reason="人工复核后追加预算")
    assert upd.old_max_tokens == 900
    assert upd.new_max_tokens == 2000
    assert upd.task_root == three_root

    report1 = build_report(three_root)
    assert report1.phase == "ok"                      # 加完预算不再停
    assert report1.gate["max_tokens"] == 2000

    # ---- 阶段2：resume（不重跑 m01/m02，只跑 m03）----
    result2 = resume(three_root, executor_driver=harness.make_executor(),
                     auditor_driver=harness.make_auditor(),
                     meter=EventLogTokenMeter(three_root))
    assert result2.status == "complete", result2.to_dict()
    assert result2.exit_reason == "all_modules_done"

    # 零重跑：exec/audit 序列仍各 3 次（m01/m02 各 1 次 + m03 1 次）
    assert harness.exec_calls == [
        {"mid": "m01", "round": 1}, {"mid": "m02", "round": 1}, {"mid": "m03", "round": 1},
    ]
    assert harness.audit_calls == [
        {"mid": "m01", "round": 1}, {"mid": "m02", "round": 1}, {"mid": "m03", "round": 1},
    ]
    # m03 是 resume 后才第一次跑 → executor_round=1（不重置、不多跑）
    assert result2.modules["m03"]["executor_round"] == 1
    # token 续接：900（阶段1）+ m03(300+100) = 1300
    assert result2.tokens_used == 1300
    snap = read_snapshot(three_root)
    assert snap["status"] == "complete"
    assert snap["budget_used_tokens"] == 1300


def test_resume_with_extra_budget_flag(three_root):
    """resume 的 --extra-budget 语义：不预先 add_budget，resume 时一步加预算续跑。"""
    from helpers import make_harness
    harness = make_harness(EXEC_TOKENS, AUDIT_TOKENS)
    result1 = _run_first_phase(three_root, harness)
    assert result1.status == "stopped"

    result2 = resume(three_root, extra_max_tokens=2000,
                     executor_driver=harness.make_executor(),
                     auditor_driver=harness.make_auditor())
    assert result2.status == "complete", result2.to_dict()
    assert harness.exec_calls == [
        {"mid": "m01", "round": 1}, {"mid": "m02", "round": 1}, {"mid": "m03", "round": 1},
    ]


def test_resume_rejects_archived(three_root):
    """放弃归档后 resume 拒绝（防误续跑）。"""
    from helpers import make_harness
    from fw_budget.manage import archive
    harness = make_harness(EXEC_TOKENS, AUDIT_TOKENS)
    _run_first_phase(three_root, harness)
    arch = archive(three_root, reason="预算不够，放弃")
    assert not three_root.exists()
    assert arch.new_path.exists()
    try:
        resume(three_root, executor_driver=harness.make_executor(),
               auditor_driver=harness.make_auditor())
        raise AssertionError("归档任务 resume 应拒绝")
    except Exception as e:
        assert "已归档" in str(e)
