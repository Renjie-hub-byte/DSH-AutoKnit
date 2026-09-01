"""complete_and_archive 单元：归档机制复用 fw-budget + 语义修正 + 拒绝续跑。"""
from __future__ import annotations

import json

import pytest

from helpers import build_task, conforming_executor, module, module_dir, run_runner_inline
from fw_integrate.archive import IntegrateFailed, complete_and_archive
from fw_integrate.context import load_integrate_context


def _full_root(tmp_path):
    mods = [module("m01", "甲", deps=[],
                   interfaces=[{"path": "/api/order/*", "method": ["POST", "PUT"], "note": "写入"}]),
            module("m02", "乙", deps=["m01"],
                   interfaces=[{"path": "/api/order/*", "method": ["GET"], "note": "查询"}])]
    baseline = {"will_have": [
        "订单数据落盘为 JSON（src/data/orders.json 结构按契约）",
        "清洗模块产出标准化订单记录（含字段校验）",
    ], "will_not_have": ["不做实时流式处理", "不做支付与风控联动"]}
    root = build_task(tmp_path, "归档单元", mods, baseline=baseline)
    result = run_runner_inline(root, conforming_executor(root))
    assert result.status == "complete"
    return root


def test_archive_reuses_fw_budget_mechanism(tmp_path):
    root = _full_root(tmp_path)
    res = complete_and_archive(root, reason="归档单元复现")
    assert res.status == "completed" and res.ok
    new_root = res.archived_path
    assert (new_root / "ARCHIVE.md").is_file()          # fw-budget 机制产物
    snap = json.loads((new_root / "总日志" / "快照.json").read_text(encoding="utf-8"))
    assert snap["status"] == "archived"
    assert snap["cause"] == "completed"                  # 完成语义修正（非 budget_abandoned）
    assert (new_root / "完成报告.md").is_file()


def test_archived_task_resume_rejected(tmp_path):
    """归档后 resume 被拒（fw-budget 语义：不能续跑已归档任务）。"""
    root = _full_root(tmp_path)
    complete_and_archive(root, reason="归档后拒续跑")
    new_root = sorted((root.parent / "archived").iterdir())[-1]
    from fw_budget.manage import BudgetManageError, resume
    with pytest.raises(BudgetManageError):
        resume(new_root, executor_driver=conforming_executor(new_root))


def test_incomplete_snapshot_rejected(tmp_path):
    """快照非 complete（从未跑完）→ complete 拒绝（input 语义）。"""
    mods = [module("m01", "甲", deps=[])]
    root = build_task(tmp_path, "归档-未跑完", mods)
    from fw_integrate.context import IntegrateInputError
    with pytest.raises(IntegrateInputError) as ei:
        complete_and_archive(root)
    assert "complete" in str(ei.value)


def test_bad_contract_area_missing(tmp_path):
    mods = [module("m01", "甲", deps=[])]
    root = build_task(tmp_path, "归档-无契约区", mods)
    import shutil
    shutil.rmtree(root / "contracts")
    from fw_integrate.context import IntegrateInputError
    with pytest.raises(IntegrateInputError) as ei:
        complete_and_archive(root)
    assert "契约区" in str(ei.value)
