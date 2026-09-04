"""心跳守护：连续 N 轮无实质产出 → 判静默卡死 → 进升级链（v1.0 含 SPLIT 路由）。

- 纯函数：detect_stall / should_escalate（单元级）
- 集成：executor 全程无实质产出 + auditor block；heartbeat_n=2 时心跳判定先于 auditor
  block 的普通路由触发（连续 2 轮卡死即升级），最终沿升级链换人/回人
"""
from __future__ import annotations

import json

from helpers import unavailable_split_driver
from fw_runner.heartbeat import detect_stall, should_escalate
from fw_runner.model import DriverOutcome
from fw_runner.runner import run


def test_detect_stall_unit():
    fp = "abc"
    assert detect_stall(fp, fp) is True        # 无变化 → 卡死一轮
    assert detect_stall(fp, "abd") is False    # 有变化 → 实质产出
    assert should_escalate(2, 2) is True
    assert should_escalate(1, 2) is False


def test_heartbeat_stall_escalates(single_root, harness):
    """executor 无实质产出（substance=False，不写文件）+ auditor block →
    心跳在连续 N 轮卡死时触发升级链；v1.0 升级链（retry→switch→SPLIT），
    SPLIT 尝试真实拆分（缺 fw-split.sh）→ split_failed → 回人（不硬拆）。"""
    calls = {"exec": 0, "audit": 0}

    def exec_fn(ctx):
        calls["exec"] += 1
        return DriverOutcome(status="ok", substance=False, tokens=0)  # 无实质产出

    def audit_fn(ctx):
        calls["audit"] += 1
        return DriverOutcome(status="ok", verdict="block", root="self",
                             confidence=0.4, reason="auditor 判 block")

    harness.exec_fn = exec_fn
    harness.audit_fn = audit_fn
    # heartbeat_n=1：连续 1 轮无实质产出即判卡死（先于 auditor block 的普通路由，隔离心跳路径）
    result = run(single_root,
                 executor_driver=harness.make_executor(),
                 auditor_driver=harness.make_auditor(),
                 split_driver=unavailable_split_driver(),
                 overrides={"heartbeat_n_rounds": 1, "retry_before_switch": 2,
                            "max_executor_switches": 1, "executor_max_rounds": 10})

    assert result.status == "needs_human", result.to_dict()
    m = result.modules["m01"]
    assert m["executor_switches"] == 1       # 换过 1 次 executor
    assert m["block_total"] == 4             # 卡死 4 轮走链（第 4 轮 SPLIT）→ 拆分失败即回人
    # v1.0：心跳卡死沿升级链走 SPLIT 路由，拆分失败以 stall 根因回人
    assert m["root"] == "stall"
    assert "模块无法拆分" in m["reason"], m["reason"]

    # 心跳升级在审计前 → 全程无 auditor
    assert calls["exec"] == 4
    assert calls["audit"] == 0, (calls["exec"], calls["audit"])

    # dispatch 日志含 heartbeat.stall 事件
    events = [json.loads(ln) for ln in
              (single_root / "总日志" / "dispatch.jsonl").read_text(encoding="utf-8").splitlines()
              if ln.strip()]
    stall_events = [e for e in events if e["event"] == "heartbeat.stall"]
    assert len(stall_events) >= 1
    assert all(e["detail"]["n"] == 1 for e in stall_events)   # N=1（测试注入）
    # v1.0：心跳路径的 module.blocked 事件带 root=stall 且 action=split（进 SPLIT 路由）
    split_events = [e for e in events if e["event"] == "module.blocked"
                    and e["detail"].get("root") == "stall"
                    and e["detail"].get("action") == "split"]
    assert split_events, "v1.0：心跳卡死应走 SPLIT 路由"
