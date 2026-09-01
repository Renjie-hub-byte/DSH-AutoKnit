"""需求5 验收①：预算 70%（本场景 80%）时输出预警 + 各模块消耗排行。

复现路径（auditor 独立）：warn_root fixture（fw-scaffold 生成）→ 注入 tokens 的 inline
驱动跑 fw-runner → 事件流出现 budget.warn（detail.budget.warned=True + detail.ranking）→
fw_budget.build_report 独立复算 phase=warned + ranking 排序正确 → CLI status 可读。
"""
from __future__ import annotations

import json

from fw_runner.runner import run as runner_run

from fw_budget.gate_state import build_budget_gate
from fw_budget.meter import EventLogTokenMeter
from fw_budget.report import build_report

EXEC_TOKENS = {"m01": 300, "m02": 200, "m03": 150}   # m03 批2 后总 800/1000=80%
AUDIT_TOKENS = {"m01": 50, "m02": 50, "m03": 50}     # 每模块 audit 50


def test_budget_warn_with_ranking(warn_root):
    from helpers import make_harness
    harness = make_harness(EXEC_TOKENS, AUDIT_TOKENS)
    """跑完 runner：warn 事件存在、ranking 排序正确、任务 complete 不停机。"""
    result = runner_run(
        warn_root,
        executor_driver=harness.make_executor(),
        auditor_driver=harness.make_auditor(),
        budget_gate=build_budget_gate(warn_root),   # 真实闸门（Null 默认不触发）
    )
    assert result.status == "complete", result.to_dict()
    assert result.tokens_used == 800

    events = [json.loads(ln) for ln in
              (warn_root / "总日志" / "dispatch.jsonl").read_text(encoding="utf-8").splitlines()
              if ln.strip()]
    warns = [e for e in events if e["event"] == "budget.warn"]
    assert len(warns) >= 1, "事件流应出现 budget.warn"
    last = warns[-1]["detail"]
    assert last["budget"]["warned"] is True
    assert last["budget"]["stop"] is False        # 70% 预警不停机
    assert last["budget"]["ratio"] >= 0.7
    ranking = last["ranking"]
    assert ranking[0]["module"] == "m01" and ranking[0]["tokens"] == 350
    assert ranking[1]["module"] == "m02" and ranking[1]["tokens"] == 250
    assert ranking[2]["module"] == "m03" and ranking[2]["tokens"] == 200


def test_budget_report_phase_warned(warn_root):
    from helpers import make_harness
    harness = make_harness(EXEC_TOKENS, AUDIT_TOKENS)
    """fw-budget 独立复算：build_report 相位=warned、排行降序、完成模块齐全。"""
    runner_run(warn_root, executor_driver=harness.make_executor(),
               auditor_driver=harness.make_auditor(),
               budget_gate=build_budget_gate(warn_root))
    report = build_report(warn_root)
    assert report.phase == "warned"
    assert report.gate["warned"] is True and report.gate["stop"] is False
    assert report.gate["used"] == 800 and report.gate["max_tokens"] == 1000
    assert report.meter["ranking"] == [
        {"module": "m01", "tokens": 350},
        {"module": "m02", "tokens": 250},
        {"module": "m03", "tokens": 200},
    ]
    assert sorted(report.completed) == ["m01", "m02", "m03"]
    assert report.run_id != ""


def test_meter_matches_runner_ledger(warn_root):
    from helpers import make_harness
    harness = make_harness(EXEC_TOKENS, AUDIT_TOKENS)
    """事件流账本与 runner 账面一致（跨会话 token 汇总的本地等价物口径）。"""
    runner_run(warn_root, executor_driver=harness.make_executor(),
               auditor_driver=harness.make_auditor(),
               budget_gate=build_budget_gate(warn_root))
    meter = EventLogTokenMeter(warn_root)
    assert meter.total() == 800
    assert meter.per_module() == {"m01": 350, "m02": 250, "m03": 200}
    assert meter.events_seen() == 6            # 3 exec + 3 audit
    assert len(meter.ranking()) == 3
