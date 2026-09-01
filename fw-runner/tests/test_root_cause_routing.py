"""失败根因分流：upstream / contract 根因的 auditor block → 直接抛人不重试。

机器可复现断言：
- executor 只被调用 1 次、auditor 判 1 次即回人（不重试、不换人）
- executor_switches == 0、无交接 bundle（未触发换人）
- REVIEW 键 root=upstream|contract、status=needs_human
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from fw_runner.context import load_task_context
from fw_runner.drivers import InlineAgentDriver
from fw_runner.model import DriverOutcome
from fw_runner.review import read_review
from fw_runner.runner import run


@pytest.mark.parametrize("root_cause", ["upstream", "contract"])
def test_upstream_contract_thrown_to_human_no_retry(single_root, root_cause):
    calls = {"exec": 0, "audit": 0}

    def executor(ctx):
        calls["exec"] += 1
        from fw_runner.review import append_done
        append_done(ctx.module.review_path, f"exec {ctx.round_no}")
        return DriverOutcome(status="ok", substance=True, tokens=0)

    def auditor(ctx):
        calls["audit"] += 1
        return DriverOutcome(status="ok", verdict="block", root=root_cause,
                             confidence=0.6, reason=f"根因 {root_cause}：上游/契约问题",
                             blocker=f"{root_cause} blocker")

    result = run(single_root, executor_driver=InlineAgentDriver(executor),
                 auditor_driver=InlineAgentDriver(auditor))

    assert result.status == "needs_human"
    assert result.needs_human == ["m01"]
    assert calls["exec"] == 1, "不应重试 executor"
    assert calls["audit"] == 1
    m = result.modules["m01"]
    assert m["executor_switches"] == 0        # 未换人
    assert m["block_total"] == 1
    assert m["root"] == root_cause

    # 无交接 bundle（没换人）
    ctx = load_task_context(single_root)
    mdir = next(iter(ctx.modules.values())).dir
    assert list((mdir / "logs").glob("handover-*")) == []

    # REVIEW 键机器可解析
    doc = read_review(mdir / "REVIEW.md")
    assert doc.kv.get("root") == root_cause
    assert doc.kv.get("status") == "needs_human"


def test_self_root_retries_not_thrown(single_root, harness):
    """对照：root=self 不抛人，回同 executor 重试（block_total 累计）；v1.0 升级链
    switch 用尽后走 SPLIT 路由（C4 真实尝试拆分，缺 fw-split.sh → split_failed 回人）。"""
    calls = {"audit": 0}

    def audit_fn(ctx):
        calls["audit"] += 1
        return DriverOutcome(status="ok", verdict="block", root="self", confidence=0.5,
                             reason="self 根因")

    harness.audit_fn = audit_fn
    result = run(single_root, executor_driver=harness.make_executor(),
                 auditor_driver=harness.make_auditor(),
                 overrides={"retry_before_switch": 2, "max_executor_switches": 1})
    # block1→retry E1；block2→换 E2；block3→retry E2；block4→SPLIT → 拆分失败回人
    assert result.status == "needs_human"
    assert result.modules["m01"]["executor_switches"] == 1
    assert calls["audit"] == 4
