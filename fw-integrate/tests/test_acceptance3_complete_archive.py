"""需求6 验收3：全部通过 → 完成报告 + 归档。

复现形态：conform_root（真实 runner 跑完全部模块、契约/产物/基线全交付）→
complete_and_archive → 任务根移入 archived/、完成报告.md 与 ARCHIVE.md 生成、
快照 status=archived cause=completed；CLI exit 0。另验证 end_gate=always 不自动归档。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from fw_integrate.archive import complete_and_archive


def test_acceptance3_complete_archive(conform_root):
    res = complete_and_archive(conform_root, reason="验收3 自动化复现")
    assert res.ok and res.status == "completed"
    new_root = res.archived_path
    assert new_root is not None and new_root.exists()
    # 归档树内容：完成报告 + ARCHIVE.md + 快照
    assert (new_root / "完成报告.md").is_file()
    assert (new_root / "ARCHIVE.md").is_file()
    report_text = (new_root / "完成报告.md").read_text(encoding="utf-8")
    assert "集成检查结果" in report_text
    assert "匹配清单" in report_text
    snap = json.loads((new_root / "总日志" / "快照.json").read_text(encoding="utf-8"))
    assert snap["status"] == "archived"
    assert snap["cause"] == "completed"
    # integration.jsonl 追加了 passed 事件
    events = [json.loads(l) for l in (new_root / "总日志" / "integration.jsonl")
              .read_text(encoding="utf-8").splitlines() if l.strip()]
    check_events = [e for e in events if e.get("event") == "integration.check"]
    assert check_events and check_events[-1]["detail"]["status"] == "passed"
    # 原路径已不存在（已 move）
    assert not conform_root.exists()


def test_acceptance3_cli_exit0_and_archive(conform_root, tmp_path):
    # 用 CLI 全流程（scaffold→runner→complete）；2 模块任务用 2 条基线（避免 m03 项缺失）
    from helpers import build_task, module, run_runner_inline, conforming_executor
    baseline = {"will_have": [
        "订单数据落盘为 JSON（src/data/orders.json 结构按契约）",
        "清洗模块产出标准化订单记录（含字段校验）",
    ], "will_not_have": ["不做实时流式处理", "不做支付与风控联动"]}
    mods = [module("m01", "甲", deps=[],
                   interfaces=[{"path": "/api/order/*", "method": ["POST", "PUT"], "note": "写入"}]),
            module("m02", "乙", deps=["m01"],
                   interfaces=[{"path": "/api/order/*", "method": ["GET"], "note": "查询"}])]
    root = build_task(tmp_path, "验收3-CLI", mods, runtime={"max_parallel": 2}, baseline=baseline)
    r = run_runner_inline(root, conforming_executor(root))
    assert r.status == "complete"
    env = {"PYTHONPATH": ":".join(sys.path)}
    proc = subprocess.run([sys.executable, "-m", "fw_integrate.cli", "complete",
                           str(root), "--reason", "CLI 验收", "--json"],
                          capture_output=True, text=True, env=env, cwd=str(root.parent))
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert out["status"] == "completed"
    assert out["archived_path"] and "archived" in out["archived_path"]
    assert (Path(out["completion_report"])).is_file()


def test_end_gate_always_needs_confirmation(conform_root):
    """end_gate=always：全部通过但要求人工确认 → 不自动归档，exit 2 语义。"""
    import yaml
    task_yaml = conform_root / "task.yaml"
    doc = yaml.safe_load(task_yaml.read_text(encoding="utf-8"))
    doc["runtime"]["end_gate"] = "always"
    task_yaml.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False),
                         encoding="utf-8")
    res = complete_and_archive(conform_root)
    assert res.status == "needs_confirmation"
    assert res.archived_path is None          # 不自动归档
    assert (conform_root / "完成报告.md").is_file()
    text = (conform_root / "完成报告.md").read_text(encoding="utf-8")
    assert "等待人工确认" in text


def test_failed_checks_no_archive(demo_root):
    """基线缺失（未交付）→ 集成失败：不归档，抛 IntegrateFailed。"""
    make_demo_complete(demo_root)
    from fw_integrate.archive import IntegrateFailed
    with pytest.raises(IntegrateFailed) as ei:
        complete_and_archive(demo_root)
    assert "缺失" in str(ei.value)
    # 未归档：原路径还在
    assert demo_root.exists()


def make_demo_complete(root):
    from helpers import make_complete_snapshot, set_review_status_done
    make_complete_snapshot(root)
    set_review_status_done(root)
