"""出口判定测试（2026-08-25 重构：remaining 事实源 = 任务书 remaining_estimate，程序读，不靠 executor 自报）。

覆盖分支：
- 无 remaining_estimate → 本块即全量，done
- remaining_estimate ≤ split_exit_threshold → final_block 事件 + 同 executor 续做一轮 → done（不静默丢活，不死循环）
- remaining_estimate > 阈值 → split 尝试；无 split driver → split_failed 回人；depth 到顶 → split_depth_cap 回人

executor 不再自报 remaining_lines（remaining 不是它的活，是 planner 定的）——该字段已废弃。
"""
from __future__ import annotations

import json

from fw_runner.model import DriverOutcome
from fw_runner.runner import run
from fw_runner.drivers import InlineAgentDriver

from helpers import build_task, module, unavailable_split_driver


def _mk_drivers():
    """executor 写产物 + substance=True（不再报 remaining_lines）；auditor 恒 pass。"""

    def executor(ctx):
        (ctx.module.dir / "src" / "out.txt").write_text("x\n", encoding="utf-8")
        return DriverOutcome(status="ok", substance=True)

    def auditor(ctx):
        return DriverOutcome(status="ok", verdict="pass", evidence_level="L2", root="", confidence=0.9,
                             reason="出口判定测试：auditor pass")

    return InlineAgentDriver(executor), InlineAgentDriver(auditor)


def _build_root(tmp_path, estimate_lines):
    mod = module("m01", "出口样本", deps=[],
                 remaining_estimate={"scope": "剩余部分", "estimate_lines": estimate_lines})
    return build_task(tmp_path, "出口判定", [mod])


def _dispatch_events(task_root) -> list[dict]:
    p = task_root / "总日志" / "dispatch.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def test_exit_no_remaining_done(tmp_path):
    """任务书无 remaining_estimate → 本块即全量，pass 即 done。"""
    root = build_task(tmp_path, "无剩余", [module("m01", "整块", deps=[])])
    e, a = _mk_drivers()
    r = run(root, executor_driver=e, auditor_driver=a)
    assert r.status == "complete"
    assert r.completed == ["m01"]


def test_exit_below_threshold_final_then_done(tmp_path):
    """remaining 300（≤1000）→ final_block 事件 + 同 executor 续做一轮 → done（不静默丢活，不死循环）。"""
    root = _build_root(tmp_path, 300)
    e, a = _mk_drivers()
    r = run(root, executor_driver=e, auditor_driver=a)
    assert r.status == "complete"
    assert r.completed == ["m01"]
    final = [ev for ev in _dispatch_events(root) if ev.get("event") == "module.final_block"]
    assert final, "应触发 module.final_block 事件"
    assert final[-1]["detail"]["remaining_lines"] == 300


def test_exit_above_threshold_split_fail_human(tmp_path):
    """remaining 1500（>1000）→ 尝试 split；测试环境无 split driver → split_failed → 回人（不静默 done）。"""
    root = _build_root(tmp_path, 1500)
    e, a = _mk_drivers()
    r = run(root, executor_driver=e, auditor_driver=a,
                 split_driver=unavailable_split_driver())
    assert r.status == "needs_human"
    assert r.completed == []
    evs = _dispatch_events(root)
    assert any(ev.get("event") == "module.split_failed" for ev in evs), "应尝试 split 并失败"


def test_exit_above_threshold_depth_cap_human(tmp_path):
    """remaining 1500 且 split 深度到上限（overrides split_max_depth=0）→ split_depth_cap → 回人。"""
    root = _build_root(tmp_path, 1500)
    e, a = _mk_drivers()
    r = run(root, executor_driver=e, auditor_driver=a, overrides={"split_max_depth": 0})
    assert r.status == "needs_human"
    evs = _dispatch_events(root)
    assert any(ev.get("event") == "module.split_depth_cap" for ev in evs)


def test_final_block_injects_remaining_to_executor(tmp_path):
    """v1.3（2026-08-27）：final_block 收官轮，runner 把 remaining 注入 executor env
    （FW_FINAL_BLOCK/FW_REMAINING_SCOPE）——剩余被吞缺陷修复的回归测试。"""
    root = _build_root(tmp_path, 300)
    seen: list[dict] = []

    def executor(ctx):
        seen.append(dict(ctx.env))
        (ctx.module.dir / "src" / "out.txt").write_text("x\n", encoding="utf-8")
        return DriverOutcome(status="ok", substance=True)

    def auditor(ctx):
        return DriverOutcome(status="ok", verdict="pass", evidence_level="L2", root="", confidence=0.9,
                             reason="pass")

    r = run(root, executor_driver=InlineAgentDriver(executor), auditor_driver=InlineAgentDriver(auditor))
    assert r.status == "complete"
    assert len(seen) == 2, "首发轮 + final 收官轮共 2 轮 executor"
    final_env = seen[-1]
    assert final_env.get("FW_FINAL_BLOCK") == "1", "收官轮 executor 必须收到 FW_FINAL_BLOCK"
    assert "剩余部分" in final_env.get("FW_REMAINING_SCOPE", ""), "收官轮 executor 必须收到 remaining scope"
    assert "FW_FINAL_BLOCK" not in seen[0], "首发轮不应有 FW_FINAL_BLOCK"


def test_final_block_human_pending_not_block(tmp_path):
    """2026-08-27 修正：final 收官轮 auditor 挂 human_pending（人工验收项：GUI/集成）→ **不阻塞 done**，
    剩余完整性由 auditor 验收项保证（未做=partial/block），human_pending 交 end_gate 人工。"""
    root = _build_root(tmp_path, 300)

    def executor(ctx):
        (ctx.module.dir / "src" / "out.txt").write_text("x\n", encoding="utf-8")
        return DriverOutcome(status="ok", substance=True)

    def auditor(ctx):
        return DriverOutcome(status="ok", verdict="pass", evidence_level="L2", root="",
                             confidence=0.9, reason="pass", human_pending=["GUI 视觉效果交真人"])

    r = run(root, executor_driver=InlineAgentDriver(executor), auditor_driver=InlineAgentDriver(auditor))
    assert r.status == "complete", "human_pending（人工验收项）不应阻塞 done"
