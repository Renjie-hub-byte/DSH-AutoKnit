"""预算闸门钩子（fw-budget 主体未实现；此处验证 runner 侧闸门逻辑与硬停/预警路径）。

- warn：check() 比例 ≥ warn_at → budget.warn 事件（含各模块消耗排行）
- stop：≥ stop_at 或单模块超限 → status=stopped + 快照 cause=budget_stop（信息完备）
- NullBudgetGate 默认不触发
"""
from __future__ import annotations

import json

from fw_runner.budget_hook import BudgetGate, NullBudgetGate
from fw_runner.model import DriverOutcome
from fw_runner.runner import run


def test_budget_warn_event(single_root, harness, tmp_path):
    gate = BudgetGate(max_tokens=100, warn_at=0.5, stop_at=1.0, per_module_max_tokens=10000)
    from fw_runner.drivers import InlineAgentDriver

    def executor(ctx):
        from fw_runner.review import append_done
        append_done(ctx.module.review_path, f"exec {ctx.round_no}")
        return DriverOutcome(status="ok", substance=True, tokens=60)  # 60 >= 50 → warn

    def auditor(ctx):
        return DriverOutcome(status="ok", verdict="pass", evidence_level="L2", root="", confidence=0.9, tokens=10)

    result = run(single_root, executor_driver=InlineAgentDriver(executor),
                 auditor_driver=InlineAgentDriver(auditor), budget_gate=gate)

    assert result.status == "complete"      # warn 不硬停
    assert result.tokens_used == 70
    events = [json.loads(ln) for ln in
              (single_root / "总日志" / "dispatch.jsonl").read_text(encoding="utf-8").splitlines()
              if ln.strip()]
    warns = [e for e in events if e["event"] == "budget.warn"]
    assert len(warns) == 1
    assert warns[0]["detail"]["budget"]["warned"] is True
    assert "ranking" in warns[0]["detail"] and warns[0]["detail"]["ranking"][0]["module"] == "m01"


def test_budget_stop_hard_stop(single_root, harness):
    gate = BudgetGate(max_tokens=100, warn_at=0.7, stop_at=1.0, per_module_max_tokens=10000)
    from fw_runner.drivers import InlineAgentDriver

    def executor(ctx):
        from fw_runner.review import append_done
        append_done(ctx.module.review_path, f"exec {ctx.round_no}")
        return DriverOutcome(status="ok", substance=True, tokens=120)  # 120 >= 100 → 硬停

    def auditor(ctx):
        return DriverOutcome(status="ok", verdict="pass", evidence_level="L2", root="", confidence=0.9, tokens=0)

    result = run(single_root, executor_driver=InlineAgentDriver(executor),
                 auditor_driver=InlineAgentDriver(auditor), budget_gate=gate)

    assert result.status == "stopped"
    assert result.exit_reason == "budget_stop"
    assert "budget" in result.payload
    assert result.payload["budget"]["stop"] is True
    # 快照 cause 记录（信息完备：完成/未完成/已试/token）
    import json as _json
    from fw_runner.checkpoint import read_snapshot
    snap = read_snapshot(single_root)
    assert snap["status"] == "stopped"
    assert snap["cause"] == "budget_stop"
    assert "加预算后 --resume-from-checkpoint" in snap["note"]


def test_budget_per_module_limit(single_root, harness):
    """单模块超限 → 硬停（防失控模块吃光全局）。"""
    gate = BudgetGate(max_tokens=10000, warn_at=0.7, stop_at=1.0, per_module_max_tokens=500)
    from fw_runner.drivers import InlineAgentDriver

    def executor(ctx):
        from fw_runner.review import append_done
        append_done(ctx.module.review_path, f"exec {ctx.round_no}")
        return DriverOutcome(status="ok", substance=True, tokens=600)  # ≥ per_module 500

    def auditor(ctx):
        return DriverOutcome(status="ok", verdict="pass", evidence_level="L2", root="", confidence=0.9, tokens=0)

    result = run(single_root, executor_driver=InlineAgentDriver(executor),
                 auditor_driver=InlineAgentDriver(auditor), budget_gate=gate)
    assert result.status == "stopped", result.to_dict()
    assert "单模块超限" in result.payload["budget"]["message"]


def test_null_budget_gate_no_trigger(single_root, harness):
    result = run(single_root, executor_driver=harness.make_executor(),
                 auditor_driver=harness.make_auditor())
    assert result.status == "complete"
