"""集成验收钩子（fw-integrate 主体未实现）：runner 调用钩子并处理 failed/end_gate。"""
from __future__ import annotations

import json

from fw_runner.integrate_hook import IntegrationHook, IntegrationReport
from fw_runner.runner import run


class _FailHook(IntegrationHook):
    def run(self, ctx, state):
        return IntegrationReport(status="failed",
                                 notes=["契约运行时校验失败：接口不匹配"],
                                 summary={"mismatch": "m01 /api/order/* POST vs m02"})

class _PassHook(IntegrationHook):
    def run(self, ctx, state):
        return IntegrationReport(status="passed",
                                 notes=["预测基线对照通过"], summary={"baseline": "ok"})


def test_integration_failed_goes_human(single_root, harness):
    result = run(single_root, executor_driver=harness.make_executor(),
                 auditor_driver=harness.make_auditor(), integration_hook=_FailHook())
    assert result.status == "integration_failed"
    assert result.integration["status"] == "failed"
    # integration.jsonl 追加了 integration.check
    lines = [json.loads(ln) for ln in
             (single_root / "总日志" / "integration.jsonl").read_text(encoding="utf-8").splitlines()
             if ln.strip()]
    check = [l for l in lines if l.get("event") == "integration.check"]
    assert len(check) == 1 and check[0]["detail"]["status"] == "failed"


def test_integration_passed_complete(single_root, harness):
    result = run(single_root, executor_driver=harness.make_executor(),
                 auditor_driver=harness.make_auditor(), integration_hook=_PassHook())
    assert result.status == "complete"
    assert result.integration["status"] == "passed"


def test_end_gate_always_needs_confirmation(single_root, harness):
    result = run(single_root, executor_driver=harness.make_executor(),
                 auditor_driver=harness.make_auditor(),
                 overrides={"end_gate": "always"})
    assert result.status == "needs_confirmation"
    assert result.exit_reason == "end_gate_always"
    assert sorted(result.completed) == ["m01"]
