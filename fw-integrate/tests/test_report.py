"""IntegrationCheckReport / 完成报告 / integration.jsonl 事件。"""
from __future__ import annotations

import json

from helpers import build_task, conforming_executor, make_complete_snapshot, module, \
    run_runner_inline, set_review_status_done, write_task_doc
from fw_integrate.context import load_integrate_context
from fw_integrate.report import append_integration_event, build_completion_report, \
    next_integration_seq, run_checks


def _root(tmp_path):
    mods = [module("m01", "甲", deps=[]), module("m02", "乙", deps=["m01"])]
    return build_task(tmp_path, "报告单元", mods)


def test_report_to_dict_shape(tmp_path):
    root = _root(tmp_path)
    make_complete_snapshot(root)
    set_review_status_done(root)
    ic = load_integrate_context(root, require_complete=False)
    report = run_checks(ic)
    d = report.to_dict()
    for key in ("ok", "errors", "warnings", "interface", "data_format",
                "data_dependency", "baseline", "summary"):
        assert key in d
    s = report.summary()
    assert "matched" in s and "missing" in s


def test_completion_report_markdown(tmp_path):
    root = _root(tmp_path)
    make_complete_snapshot(root)
    set_review_status_done(root)
    ic = load_integrate_context(root, require_complete=False)
    report = run_checks(ic)
    md = build_completion_report(ic, report, status="completed")
    assert "# 完成报告" in md
    assert "集成检查结果" in md
    assert "匹配清单" in md
    assert "end_gate 决定" in md


def test_integration_event_append_seq(tmp_path):
    root = _root(tmp_path)
    make_complete_snapshot(root, run_id="run-report-1")
    set_review_status_done(root)
    ic = load_integrate_context(root, require_complete=False)
    report = run_checks(ic)
    assert next_integration_seq(root) == 1
    append_integration_event(root, report, end_gate="auto", run_id="run-report-1")
    assert next_integration_seq(root) == 2
    events = [json.loads(l) for l in (root / "总日志" / "integration.jsonl")
              .read_text(encoding="utf-8").splitlines() if l.strip()]
    last = events[-1]
    assert last["event"] == "integration.check"
    assert last["seq"] == 1 and last["run_id"] == "run-report-1"
    assert last["detail"]["status"] in ("passed", "failed")


def test_real_runner_produces_integration_jsonl(tmp_path):
    """真实 runner（无钩子）会写 deferred integration.check 事件；fw-integrate 续接 seq。"""
    baseline = {"will_have": [
        "订单数据落盘为 JSON（src/data/orders.json 结构按契约）",
        "清洗模块产出标准化订单记录（含字段校验）",
    ], "will_not_have": ["不做实时流式处理", "不做支付与风控联动"]}
    mods = [module("m01", "甲", deps=[]), module("m02", "乙", deps=["m01"])]
    root = build_task(tmp_path, "报告-真实runner", mods, baseline=baseline)
    result = run_runner_inline(root, conforming_executor(root))
    assert result.status == "complete"
    events = [json.loads(l) for l in (root / "总日志" / "integration.jsonl")
              .read_text(encoding="utf-8").splitlines() if l.strip()]
    assert any(e.get("event") == "integration.check" and e.get("detail", {}).get("status") == "deferred"
               for e in events)
    ic = load_integrate_context(root, require_complete=True)
    report = run_checks(ic)
    assert report.ok
    append_integration_event(root, report, end_gate="auto", run_id=result.run_id)
    events2 = [json.loads(l) for l in (root / "总日志" / "integration.jsonl")
               .read_text(encoding="utf-8").splitlines() if l.strip()]
    last = events2[-1]
    assert last["detail"]["status"] == "passed"
    # seq 续接（下一事件 > 之前所有 seq）
    seqs = [int(e["seq"]) for e in events2 if e.get("seq") is not None]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
