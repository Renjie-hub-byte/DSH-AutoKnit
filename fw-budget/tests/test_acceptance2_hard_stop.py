"""需求5 验收②：100% 或单模块超限 → 硬停 + 抛人（信息完备：完成/未完成/已试轮数/token）。

复现路径（auditor 独立）：stop_root / per_module_stop_root → inline 驱动跑 fw-runner →
status=stopped + 快照 cause=budget_stop + 事件 budget.stop → build_report 输出信息完备。
"""
from __future__ import annotations

import json

from fw_runner.checkpoint import read_snapshot
from fw_runner.runner import run as runner_run

from fw_budget.gate_state import build_budget_gate
from fw_budget.report import build_report


def test_global_max_tokens_hard_stop(stop_root):
    from helpers import make_harness
    harness = make_harness(exec_tokens={"m01": 600, "m02": 0})
    """全局 100% 硬停：completed/unfinished/tried/token 信息完备。"""
    result = runner_run(stop_root, executor_driver=harness.make_executor(),
                        auditor_driver=harness.make_auditor(),
                        budget_gate=build_budget_gate(stop_root))
    assert result.status == "stopped", result.to_dict()
    assert result.exit_reason == "budget_stop"
    assert result.tokens_used == 600
    assert "budget" in result.payload and result.payload["budget"]["stop"] is True

    snap = read_snapshot(stop_root)
    assert snap["status"] == "stopped"
    assert snap["cause"] == "budget_stop"
    assert "加预算后 --resume-from-checkpoint" in snap["note"]

    # 事件流证据
    events = [json.loads(ln) for ln in
              (stop_root / "总日志" / "dispatch.jsonl").read_text(encoding="utf-8").splitlines()
              if ln.strip()]
    stops = [e for e in events if e["event"] == "budget.stop"]
    assert len(stops) == 1
    assert stops[0]["detail"]["budget"]["stop"] is True
    assert stops[0]["detail"]["budget"]["used"] == 600

    # 信息完备报告：完成/未完成/已试/token
    report = build_report(stop_root)
    assert report.phase == "stopped"
    assert report.gate["stop"] is True
    assert report.gate["used"] == 600
    assert report.gate["max_tokens"] == 500
    assert report.completed == ["m01"]                 # m01 已完成进入 done
    assert report.unfinished == ["m02"]                # m02 未开始（pending）
    assert report.tried == {"m01": 1, "m02": 0}        # 已试轮数（executor_round）
    assert report.meter["total"] == 600
    assert report.meter["ranking"][0] == {"module": "m01", "tokens": 600}
    assert report.snapshot_status == "stopped"
    assert report.run_id == result.run_id


def test_per_module_max_tokens_hard_stop(per_module_stop_root):
    from helpers import make_harness
    harness = make_harness(exec_tokens={"m01": 600, "m02": 0})
    """单模块超限 → 硬停 + 抛人（防失控模块吃光全局）。"""
    result = runner_run(per_module_stop_root, executor_driver=harness.make_executor(),
                        auditor_driver=harness.make_auditor(),
                        budget_gate=build_budget_gate(per_module_stop_root))
    assert result.status == "stopped", result.to_dict()
    assert "单模块超限" in result.payload["budget"]["message"]
    report = build_report(per_module_stop_root)
    assert report.phase == "stopped"
    assert "单模块超限" in report.stop_message
    # 全局远未到 100%，但单模块 600 >= per_module 500
    assert report.gate["ratio"] < 0.7
