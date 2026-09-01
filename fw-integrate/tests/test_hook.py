"""FwIntegrateHook 对接 fw-runner：全流程集成（真实 runner + 集成钩子）。

- 一致根 + 钩子 → runner status=complete，integration 事件 status=passed
- 篡改根（接口不匹配/基线缺失） + 钩子 → runner status=integration_failed（exit 2 语义）
"""
from __future__ import annotations

import json

from fw_runner.runner import run as runner_run

from helpers import (fill_contract, module, module_dir, conforming_executor, conforming_auditor,
                     build_task, write_task_doc)
from fw_integrate.hook import FwIntegrateHook


def test_hook_passed_full_run(tmp_path):
    mods = [module("m01", "甲", deps=[],
                   interfaces=[{"path": "/api/order/*", "method": ["POST", "PUT"], "note": "写入"}]),
            module("m02", "乙", deps=["m01"],
                   interfaces=[{"path": "/api/order/*", "method": ["GET"], "note": "查询"}])]
    baseline = {"will_have": [
        "订单数据落盘为 JSON（src/data/orders.json 结构按契约）",
        "清洗模块产出标准化订单记录（含字段校验）",
    ], "will_not_have": ["不做实时流式处理", "不做支付与风控联动"]}
    root = build_task(tmp_path, "钩子-通过", mods, baseline=baseline)
    result = runner_run(root, executor_driver=conforming_executor(root),
                        auditor_driver=conforming_auditor(),
                        integration_hook=FwIntegrateHook())
    assert result.status == "complete"
    assert result.integration and result.integration.get("status") == "passed"
    events = [json.loads(l) for l in (root / "总日志" / "integration.jsonl")
              .read_text(encoding="utf-8").splitlines() if l.strip()]
    chk = [e for e in events if e.get("event") == "integration.check"]
    assert chk and chk[-1]["detail"]["status"] == "passed"


def test_hook_failed_on_interface_mismatch(tmp_path):
    mods = [module("m01", "甲", deps=[],
                   interfaces=[{"path": "/api/order/*", "method": ["POST", "PUT"], "note": "写入"}]),
            module("m02", "乙", deps=["m01"],
                   interfaces=[{"path": "/api/order/*", "method": ["GET"], "note": "查询"}])]
    root = build_task(tmp_path, "钩子-接口不匹配", mods)
    # 预先把 m02 的 contract.yaml 篡改为抢占 m01 的 POST（executor 不覆盖 read_api）
    fill_contract(module_dir(root, "m02"), ["m01"], [], "数据",
                  read_api_add={"path": "/api/order/*", "method": ["POST"]})
    result = runner_run(root, executor_driver=conforming_executor(root),
                        auditor_driver=conforming_auditor(),
                        integration_hook=FwIntegrateHook())
    assert result.status == "integration_failed"
    notes = "\n".join((result.integration or {}).get("notes") or [])
    assert "m01" in notes and "m02" in notes


def test_hook_failed_on_baseline_missing(tmp_path):
    """演示 executor（不交付契约产物）→ 基线缺失 → 钩子 failed → integration_failed。"""
    from helpers import PRODUCER_ARTIFACTS, conforming_executor as real_exec
    mods = [module("m01", "甲", deps=[]), module("m02", "乙", deps=["m01"])]
    root = build_task(tmp_path, "钩子-基线缺失", mods)   # 默认 3 项 will_have
    # 用“空交货”executor：只写 REVIEW 已做，不落契约产物
    from fw_runner.drivers import InlineAgentDriver
    from fw_runner.model import DriverOutcome
    from fw_runner.review import append_done

    def empty_exec(ctx):
        append_done(module_dir(root, ctx.module.id) / "REVIEW.md",
                    f"{ctx.module.id} 执行（未交付契约产物）")
        return DriverOutcome(status="ok", substance=True, tokens=0)

    result = runner_run(root, executor_driver=InlineAgentDriver(empty_exec),
                        auditor_driver=conforming_auditor(),
                        integration_hook=FwIntegrateHook())
    assert result.status == "integration_failed"
    notes = "\n".join((result.integration or {}).get("notes") or [])
    assert "缺失" in notes
