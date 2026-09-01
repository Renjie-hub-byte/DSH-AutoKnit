"""可靠性补丁（功能A）：executor 进度落档 —— 轮数将尽/失败不丢半成品。

修复的真实运行教训：executor 轮数用尽时直接半途而废，不留下「完成了什么、还剩什么」，
换人后只能从头重干。本测试证明：
1. write/read 进度快照（交付说明.md `## 进度快照` 小节）可机器解析、可幂等覆盖；
2. ensure_progress 兜底：无快照 → 写占位「executor 未落档进度，视为中断」，已有则不覆盖；
3. REVIEW.md 已做/待办 兜底：交付说明.md 无快照时也能给出 已完成/剩余；
4. 集成：executor 轮数将尽（剩余 ≤1 轮）→ runner 自动兜底落档；
5. 集成：executor 某轮失败/超时 → 同样落档（可 resume 续跑）。
"""
from __future__ import annotations

import json

from fw_runner.context import load_task_context
from fw_runner.drivers import InlineAgentDriver
from fw_runner.model import DriverOutcome
from fw_runner.progress import (
    STALE_MARKER,
    ensure_progress,
    progress_briefing,
    progress_snapshot,
    read_progress,
    write_progress,
)
from fw_runner.review import append_done, append_todo, read_review
from fw_runner.runner import run


def _single_spec(single_root):
    ctx = load_task_context(single_root)
    return next(iter(ctx.modules.values()))


def test_delivery_template_starts_without_progress_section(single_root):
    spec = _single_spec(single_root)
    assert spec.delivery_path.is_file()
    assert read_progress(spec.delivery_path) == {}
    assert "进度快照" not in spec.delivery_path.read_text(encoding="utf-8")


def test_write_read_progress_roundtrip(single_root):
    spec = _single_spec(single_root)
    write_progress(spec.delivery_path, done="已完成模块甲骨架", remaining="模块乙剩余实现",
                   executor_id="E1", round_no=2)
    snap = read_progress(spec.delivery_path)
    assert snap["已完成"] == "已完成模块甲骨架"
    assert snap["剩余"] == "模块乙剩余实现"
    assert snap["source"] == "delivery"
    # 其它小节（改动内容/测试结果/…）保留
    text = spec.delivery_path.read_text(encoding="utf-8")
    for sec in ("## 改动内容", "## 测试结果", "## 外部验收自测", "## 已知风险"):
        assert sec in text, f"写进度快照不应破坏 {sec}"
    # 覆盖写：小节整块替换，不重复
    write_progress(spec.delivery_path, done="D2", remaining="R2", executor_id="E1", round_no=3)
    assert read_progress(spec.delivery_path)["已完成"] == "D2"
    assert spec.delivery_path.read_text(encoding="utf-8").count("## 进度快照") == 1


def test_ensure_progress_writes_placeholder_and_is_idempotent(single_root):
    spec = _single_spec(single_root)
    assert ensure_progress(spec.delivery_path, executor_id="E1", round_no=4) is True
    snap = read_progress(spec.delivery_path)
    assert STALE_MARKER in snap["已完成"]
    assert STALE_MARKER in snap["剩余"]
    # 幂等：已有快照（哪怕 executor 写的真实进度）不再被占位覆盖
    assert ensure_progress(spec.delivery_path, executor_id="E1", round_no=5) is False
    write_progress(spec.delivery_path, done="真实进度", remaining="剩一点", executor_id="E1", round_no=5)
    assert ensure_progress(spec.delivery_path, executor_id="E1", round_no=6) is False
    assert read_progress(spec.delivery_path)["已完成"] == "真实进度"


def test_progress_snapshot_falls_back_to_review_ledger(single_root):
    """交付说明.md 无快照时：从 REVIEW.md 已做/待办 兜底（去「（占位）」行）。"""
    spec = _single_spec(single_root)
    append_done(spec.review_path, "完成 A 模块实现")
    append_todo(spec.review_path, "B 模块待实现")
    snap = progress_snapshot(spec)
    assert "完成 A 模块实现" in snap["已完成"]
    assert "B 模块待实现" in snap["剩余"]
    assert snap["source"].startswith("REVIEW")
    assert "（占位）" not in snap["已完成"] and "（占位）" not in snap["剩余"]


def test_progress_briefing_instructs_continue_not_redo(single_root):
    """功能B 提示词片段：明确「这是前任做到的位置，从剩余继续，不要重做」。"""
    spec = _single_spec(single_root)
    write_progress(spec.delivery_path, done="X", remaining="Y", executor_id="E1", round_no=2)
    brief = progress_briefing(spec, old_executor_id="E1")
    assert "前任 executor: E1" in brief
    assert "已完成: X" in brief
    assert "剩余: Y" in brief
    assert "从「剩余」继续" in brief
    assert "不要重做" in brief


def test_rounds_near_exhaustion_archives_placeholder(single_root):
    """功能A 集成：executor 不主动落档 → 轮数将尽（剩余 ≤1 轮）时 runner 兜底写占位，
    最终回人时 交付说明.md 已留下「完成了什么/剩什么」记录。"""
    exec_calls = {"n": 0}

    def executor(ctx):
        exec_calls["n"] += 1
        append_done(ctx.module.review_path, f"round {ctx.round_no} 干了点活")
        return DriverOutcome(status="ok", substance=True, tokens=0)

    def auditor(ctx):
        return DriverOutcome(status="ok", verdict="block", root="self", confidence=0.4,
                             reason="验收不过（演示常 block）", blocker="演示 blocker")

    result = run(single_root,
                 overrides={"executor_max_rounds": 3, "retry_before_switch": 99,
                            "max_executor_switches": 0},
                 executor_driver=InlineAgentDriver(executor),
                 auditor_driver=InlineAgentDriver(auditor))

    assert result.status == "needs_human"
    assert exec_calls["n"] == 3                       # 第 3 轮跑完即触顶，不给第 4 轮
    spec = _single_spec(single_root)
    snap = read_progress(spec.delivery_path)
    assert snap, "轮数将尽必须落档进度"
    assert STALE_MARKER in snap["已完成"]
    doc = read_review(spec.review_path)
    assert doc.kv.get("executor_round") == "3"
    assert doc.kv.get("status") == "needs_human"


def test_executor_round_error_archives_placeholder(single_root):
    """功能A 集成：executor 某轮返回失败（超时/异常）→ 兜底落档进度并留事件痕迹。
    v1.0：错误路径沿升级链（block/self）在 switch 用尽后走 SPLIT 路由（C4 真实尝试拆分，
    缺 fw-split.sh → split_failed）→ 回人；兜底落档仍保留。"""
    calls = {"exec": 0, "audit": 0}

    def executor(ctx):
        calls["exec"] += 1
        return DriverOutcome(status="error", root="self", reason="executor 驱动超时",
                             substance=False)

    def auditor(ctx):
        calls["audit"] += 1
        return DriverOutcome(status="ok", verdict="pass", evidence_level="L2", confidence=0.9)

    result = run(single_root, executor_driver=InlineAgentDriver(executor),
                 auditor_driver=InlineAgentDriver(auditor))

    assert result.status == "needs_human"
    assert calls["exec"] == 4            # E1 错 2 次换 E2；E2 错 2 次走 SPLIT 路由 → 拆分失败回人
    assert calls["audit"] == 0
    spec = _single_spec(single_root)
    snap = read_progress(spec.delivery_path)
    assert snap and STALE_MARKER in snap["已完成"]
    doc = read_review(spec.review_path)
    assert doc.kv.get("status") == "needs_human"
    # v1.0：SPLIT 尝试拆分失败，最终 REVIEW detail 记录拆分失败回人
    assert "模块无法拆分" in doc.kv.get("detail", "")
    events = [json.loads(ln) for ln in
              (single_root / "总日志" / "dispatch.jsonl").read_text(encoding="utf-8").splitlines()
              if ln.strip()]
    # 错误根因仍可追溯：dispatch 日志的 module.blocked 事件带 agent_error 明细
    blocked = [e for e in events if e["event"] == "module.blocked"
               and "executor 驱动超时" in e["detail"].get("reason", "")]
    assert blocked, "应能在 dispatch 日志追溯 executor 错误明细"
    # dispatch 事件含 executor.progress.archived（可追溯 runner 何时兜底落档）
    archived = [e for e in events if e["event"] == "executor.progress.archived"]
    assert archived, "应产生 executor.progress.archived 事件"
    assert archived[0]["detail"]["reason"] == "agent_error"
