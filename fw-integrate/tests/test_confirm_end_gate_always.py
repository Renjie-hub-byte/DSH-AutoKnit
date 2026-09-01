"""end_gate=always 真实链路闭环：fw-runner → needs_confirmation → fw-integrate confirm → 归档。

背景（真实缺陷修复）：fw-runner（round_004 已审计）在 end_gate=always 且集成钩子通过时，
把快照写成 status=needs_confirmation（exit 2 等待人工拍板），不会写 complete 快照；因此
fw-integrate complete（要求快照 complete）无法为这类任务出完成报告/归档。本测试验证
fw-integrate confirm 补上闭环：人工确认 → 检查全通过 → 完成报告 + 归档（exit 0）。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from fw_integrate.archive import confirm_and_archive
from fw_integrate.context import IntegrateInputError, load_integrate_context
from fw_integrate.report import run_checks

from helpers import build_task, conforming_executor, module, run_runner_inline


def _always_root(tmp_path) -> Path:
    mods = [module("m01", "数据采集", deps=[],
                   interfaces=[{"path": "/api/order/*", "method": ["POST", "PUT"],
                                "note": "订单写入"}]),
            module("m02", "数据清洗", deps=["m01"],
                   interfaces=[{"path": "/api/order/*", "method": ["GET"],
                                "note": "清洗后订单查询"}])]
    baseline = {"will_have": [
        "订单数据落盘为 JSON（src/data/orders.json 结构按契约）",
        "清洗模块产出标准化订单记录（含字段校验）",
    ], "will_not_have": ["不做实时流式处理", "不做支付与风控联动"]}
    root = build_task(tmp_path, "确认-端到端", mods, baseline=baseline,
                      runtime={"max_parallel": 2, "executor_max_rounds": 5,
                               "retry_before_switch": 2, "max_executor_switches": 1,
                               "end_gate": "always"})
    result = run_runner_inline(root, conforming_executor(root))
    assert result.status == "needs_confirmation", f"end_gate=always 应 needs_confirmation: {result.status}"
    # 快照真实写成 needs_confirmation（runner 已审计行为）
    snap = json.loads((root / "总日志" / "快照.json").read_text(encoding="utf-8"))
    assert snap["status"] == "needs_confirmation"
    return root


def test_confirm_real_runner_chain(tmp_path):
    root = _always_root(tmp_path)
    # confirm 前：complete 语义应拒绝（快照非 complete 是 fw-integrate 的防呆）
    from fw_integrate.archive import complete_and_archive
    with pytest.raises(IntegrateInputError):
        complete_and_archive(root)
    # 集成检查本身应通过（交付一致）
    ic = load_integrate_context(root, require_complete=False)
    report = run_checks(ic)
    assert report.ok
    # 人工确认 → 完成报告 + 归档
    res = confirm_and_archive(root, reason="人工确认测试")
    assert res.ok and res.status == "confirmed"
    new_root = res.archived_path
    assert new_root is not None and new_root.exists()
    assert (new_root / "完成报告.md").is_file()
    assert (new_root / "ARCHIVE.md").is_file()
    text = (new_root / "完成报告.md").read_text(encoding="utf-8")
    assert "人工已确认" in text
    snap = json.loads((new_root / "总日志" / "快照.json").read_text(encoding="utf-8"))
    assert snap["status"] == "archived"
    assert snap["cause"] == "completed"
    assert not root.exists()


def test_confirm_cli_exit0(tmp_path):
    root = _always_root(tmp_path)
    env = {"PYTHONPATH": ":".join(sys.path)}
    proc = subprocess.run([sys.executable, "-m", "fw_integrate.cli", "confirm",
                           str(root), "--reason", "CLI 确认", "--json"],
                          capture_output=True, text=True, env=env, cwd=str(root.parent))
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert out["status"] == "confirmed"
    assert out["archived_path"] and "archived" in out["archived_path"]
    assert Path(out["completion_report"]).is_file()


def test_confirm_rejects_invalid_snapshot(tmp_path):
    """快照既非 needs_confirmation 也非 complete（未跑完）→ 拒绝确认。"""
    mods = [module("m01", "甲", deps=[], interfaces=[{"path": "/api/a/*", "method": ["GET"], "note": "x"}])]
    root = build_task(tmp_path, "确认-非法", mods, runtime={"end_gate": "always"})
    # 不跑 runner：快照为脚手架初始状态（status 非 needs_confirmation/complete）
    with pytest.raises(IntegrateInputError) as ei:
        confirm_and_archive(root)
    assert "仅用于待人工确认/已完成任务" in str(ei.value)
