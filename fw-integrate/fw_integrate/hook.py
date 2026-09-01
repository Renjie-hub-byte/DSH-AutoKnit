"""fw-integrate 的 fw-runner IntegrationHook 实现（对接 round_004 已审计钩子契约）。

runner 在全部批次结束后调用 `hook.run(ctx, state)`，返回 IntegrationReport：
- status="passed"  → runner 按 end_gate 收尾（auto=complete；always=needs_confirmation）
- status="failed"  → runner 记 integration_failed（快照 + exit 2 抛人），notes 含
  “哪两个模块接口不匹配”等错误清单（验收1 在 runner 全流程中的体现）

**只判不归档**：归档（完成报告 + 移动目录）由 fw-integrate 的 complete/run 收尾阶段执行，
因为 runner 调用钩子后还会写快照与 integration 日志，钩子内移目录会破坏后续写（见 archive.py）。
"""
from __future__ import annotations

from typing import Any

from .context import load_integrate_context
from .report import run_checks


class FwIntegrateHook:
    """fw-integrate 集成验收钩子。用法：
        from fw_runner.runner import run
        from fw_integrate.hook import FwIntegrateHook
        result = run(task_root, executor_driver=..., auditor_driver=...,
                     integration_hook=FwIntegrateHook())
    """

    def run(self, ctx, state) -> Any:
        """实现 fw_runner.integrate_hook.IntegrationHook 协议。ctx 为 fw-runner TaskContext。"""
        from fw_runner.integrate_hook import IntegrationReport  # noqa: E402
        ic = load_integrate_context(ctx.task_root, require_complete=False)
        report = run_checks(ic)
        summary = report.summary()
        if report.ok:
            return IntegrationReport(
                status="passed",
                notes=["fw-integrate：集成验收全部通过（接口匹配/数据格式/数据依赖/预测基线）"]
                + report.notes,
                summary=summary,
            )
        return IntegrationReport(
            status="failed",
            notes=report.errors,
            summary=summary,
        )
