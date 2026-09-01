"""manage.py 单元测试：add-budget 原子写 / archive 归档 / resume 前置校验。"""
from __future__ import annotations

from helpers import build_task, module

from fw_budget.manage import (
    BudgetManageError, add_budget, archive, resume_advice,
)
from fw_budget.report import build_report


def test_add_budget_atomic_and_protocol_valid(tmp_path):
    """add-budget：老→新值、保留注释头、改后任务书仍通过 fw-protocol 校验。"""
    root = build_task(tmp_path, "加预算", [module("m01", "甲", deps=[])],
                      budget={"max_tokens": 500, "warn_at": 0.7, "stop_at": 1.0})
    yaml = root / "task.yaml"
    before = yaml.read_text(encoding="utf-8")
    assert before.lstrip().startswith("#")            # effective 版本带说明头

    upd = add_budget(root, 2000, reason="人工复核")
    assert upd.old_max_tokens == 500 and upd.new_max_tokens == 2000
    assert upd.reason == "人工复核"

    after = yaml.read_text(encoding="utf-8")
    assert after.lstrip().startswith("#")             # 注释头保留
    assert "# 由 fw-scaffold 从输入 task.yaml" in after or "# task.yaml" in after

    # 改后任务书重新通过 fw-protocol（resume 时 runner 会重新校验）
    from fw_protocol import validate_file
    res = validate_file(yaml)
    assert res.ok, [i.message for i in res.errors]
    # budget 语义自检：500 -> 2000 后 per_module 缺省 = max_tokens，无 warning 冲突
    assert res.effective["budget"]["max_tokens"] == 2000


def test_add_budget_rejects_bad_input(tmp_path):
    root = build_task(tmp_path, "加预算-非法", [module("m01", "甲", deps=[])],
                      budget={"max_tokens": 500})
    try:
        add_budget(root, 0)
        raise AssertionError("max_tokens=0 应拒绝")
    except BudgetManageError as e:
        assert "正整数" in str(e)


def test_archive_moves_dir_and_marks_snapshot(tmp_path):
    """archive：快照标记 archived + 目录 move 到 archived/ + ARCHIVE.md 真实证据。"""
    root = build_task(tmp_path, "归档样本", [module("m01", "甲", deps=[])],
                      budget={"max_tokens": 100})
    # 先制造快照（跑一轮？不需要——archive 对无快照也应工作，此处用 build_report 造快照）
    # 有快照更贴近真实：直接写一个初始快照形态
    import json
    (root / "总日志").mkdir(parents=True, exist_ok=True)
    (root / "总日志" / "快照.json").write_text(json.dumps({
        "schema_version": 3, "run_id": "r-arch", "task": "归档样本", "status": "stopped",
        "cause": "budget_stop", "modules": {"m01": "pending"}, "completed_order": [],
        "budget_used_tokens": 0, "last_seq": 0,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    arch = archive(root, reason="预算用尽，放弃交付")
    assert not root.exists()                                  # 原路径已移走
    assert arch.new_path.exists()                             # 新路径存在
    assert arch.new_path.parent.name == "archived"
    assert arch.snapshot_status == "archived"
    assert arch.archived_mark.is_file()
    mark = arch.archived_mark.read_text(encoding="utf-8")
    assert "放弃交付" in mark and "原路径" in mark

    # 快照在归档区标记 archived（机器可判定"
    snap = json.loads((arch.new_path / "总日志" / "快照.json").read_text(encoding="utf-8"))
    assert snap["status"] == "archived"
    assert snap["cause"] == "budget_abandoned"


def test_archive_rejects_double_archive(tmp_path):
    root = build_task(tmp_path, "重复归档", [module("m01", "甲", deps=[])])
    archive(root, reason="第一次")
    try:
        archive(root, reason="第二次")
        raise AssertionError("重复归档应拒绝")
    except BudgetManageError as e:
        assert "已归档" in str(e)


def test_resume_advice_reflects_budget(tmp_path):
    """resume_advice：预算不足 → would_stop_now=True；充足 → False。"""
    root = build_task(tmp_path, "实盘建议", [module("m01", "甲", deps=[])],
                      budget={"max_tokens": 100, "warn_at": 0.7, "stop_at": 1.0})
    # 制造 200 token 消耗事件 → 200/100=200% 立即再停
    import json
    (root / "总日志").mkdir(parents=True, exist_ok=True)
    with open(root / "总日志" / "dispatch.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"seq": 1, "run_id": "r1", "event": "executor.round.done",
                            "module": "m01", "detail": {"tokens": 200}}, ensure_ascii=False) + "\n")
    advice = resume_advice(root)
    assert advice.would_stop_now is True
    assert advice.used == 200 and advice.max_tokens == 100
    assert "立即再次硬停" in advice.message

    add_budget(root, 2000)
    advice2 = resume_advice(root)
    assert advice2.would_stop_now is False


def test_archive_rejected_by_report(tmp_path):
    """归档任务 build_report 也应可读（只读审计允许）；resume 前置负责拒绝。"""
    root = build_task(tmp_path, "归档后可读", [module("m01", "甲", deps=[])])
    arch = archive(root, reason="x")
    rep = build_report(arch.new_path)
    assert rep.snapshot_status == "archived"
