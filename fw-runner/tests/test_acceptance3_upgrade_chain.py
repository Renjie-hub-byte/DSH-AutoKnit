"""需求4 验收 3（v1.0 升级链）：auditor block 2 次 → 换 executor（读 REVIEW.md 交接）
→ SPLIT（switch 用尽后进拆分环节）→ 拆分失败（fw-split.sh 未就绪）→ 上限回人。

配置：retry_before_switch=2, max_executor_switches=1（默认），enable_split=True（默认）。
预期轮次：E1(block1 回同 E1) → E1(block2 → 换 E2) → E2(block3 回同 E2) →
E2(block4 → SPLIT) → _do_split 尝试真拆分 → 默认驱动缺 bin/fw-split.sh → split_failed → 回人。
（v1.0 C4 落地后，SPLIT 不再是"视为 retry 续跑"：真实调用 split agent，脚本缺省时回人不硬拆。）
机器可复现断言：
- executor 身份序列 == [E1,E1,E2,E2]（第 4 轮走 SPLIT 路由，尝试拆分后回人）
- E2 首轮开工时 REVIEW 已带 auditor 反馈（root=self, status=blocked）——证明读 REVIEW 交接
- 切换时生成交接三件套 bundle（logs/handover-*）
- dispatch 日志含 module.split_failed（拆分失败事件）
- 最终 needs_human；REVIEW 键 root=self / block_total=4 / status=needs_human
"""
from __future__ import annotations

import json
from pathlib import Path

from helpers import unavailable_split_driver
from fw_runner.context import load_task_context
from fw_runner.drivers import InlineAgentDriver
from fw_runner.model import DriverOutcome
from fw_runner.review import read_review
from fw_runner.runner import run


class _BlockHarness:
    def __init__(self):
        self.exec_seq: list[tuple[int, str]] = []      # (round, executor_id)
        self.review_at_start: list[dict] = []           # 每轮开工时 REVIEW 键
        self.audit_seq: list[int] = []

    def build(self):
        def executor(ctx):
            self.exec_seq.append((ctx.round_no, ctx.executor_id))
            doc = read_review(ctx.module.review_path)
            self.review_at_start.append({
                "round": ctx.round_no, "executor_id": ctx.executor_id,
                "status": doc.kv.get("status", ""), "root": doc.kv.get("root", ""),
            })
            # 干活（有实质产出；block 是 auditor 判的，与 executor 产出无关）
            from fw_runner.review import append_done
            append_done(ctx.module.review_path, f"exec {ctx.round_no} ({ctx.executor_id})")
            return DriverOutcome(status="ok", substance=True, tokens=0)

        def auditor(ctx):
            self.audit_seq.append(ctx.round_no)
            return DriverOutcome(status="ok", verdict="block", root="self",
                                 confidence=0.4, reason="验收不过：演示判定持续 block",
                                 blocker="演示 blocker")

        return InlineAgentDriver(executor), InlineAgentDriver(auditor)


def test_block_twice_then_switch_then_human(single_root):
    h = _BlockHarness()
    exec_driver, aud_driver = h.build()

    result = run(single_root, executor_driver=exec_driver, auditor_driver=aud_driver,
        split_driver=unavailable_split_driver())

    # 1) 升级链轮次与 executor 身份：block 2 次 → 换 executor → SPLIT 尝试拆分 → 回人
    assert h.exec_seq == [(1, "E1"), (2, "E1"), (3, "E2"), (4, "E2")], h.exec_seq
    assert len(h.audit_seq) == 4  # 4 次 block 判定（第 4 次走 SPLIT 路由）后拆分失败回人

    # 2) 新 executor 开工读 REVIEW.md 交接：E2 首轮看到 auditor 反馈（root=self, status=blocked）
    e2_first = [r for r in h.review_at_start if r["executor_id"] == "E2"][0]
    assert e2_first["root"] == "self"
    assert e2_first["status"] == "blocked"

    # 3) 切换时生成交接三件套 bundle
    ctx = load_task_context(single_root)
    bundles = list((single_root / "modules" / next(iter(ctx.modules.values())).dir.name / "logs").glob("handover-*"))
    assert len(bundles) == 1, f"预期 1 个交接 bundle，实际 {len(bundles)}"

    # 4) 拆分失败回人：status=needs_human，信息完备；dispatch 含 module.split_failed
    assert result.status == "needs_human"
    assert result.needs_human == ["m01"]
    m = result.modules["m01"]
    assert m["executor_switches"] == 1
    assert m["block_total"] == 4            # 4 次 block 走链（含 SPLIT 路由），拆分失败即回人
    assert m["root"] == "self"
    events = [json.loads(ln) for ln in
              (single_root / "总日志" / "dispatch.jsonl").read_text(encoding="utf-8").splitlines()
              if ln.strip()]
    split_failed = [e for e in events if e["event"] == "module.split_failed"]
    assert split_failed, "SPLIT 路由应尝试真实拆分（缺 fw-split.sh → split_failed）"
    assert split_failed[0]["detail"]["parent"] == "m01"

    # 5) REVIEW 键值行机器可解析
    doc = read_review(next(iter(ctx.modules.values())).review_path)
    assert doc.kv.get("status") == "needs_human"
    assert doc.kv.get("block_total") == "4"
    assert doc.kv.get("root") == "self"
