"""fw-budget —— 预算闸门模块（需求5）。

在 dsh 之上的任务编排层 framework-v1 中负责预算管理：
- TokenMeter        记账源抽象（dsh token-meter 跨会话统计适配点 + 本地事件流账本等价物）
- BudgetGate 状态    复用 fw-runner（已审计）的闸门逻辑；gates 重建支持 resume 不失忆
- BudgetReport       信息完备的状态报告（完成/未完成/已试/token/排行/warn/stop）
- add_budget/archive/resume  人工加预算 / 放弃归档 / 续跑
"""
from __future__ import annotations

from .meter import DshTokenMeter, EventLogTokenMeter, TokenMeter, summarize  # noqa: F401
from .report import BudgetReport, build_report, human_summary  # noqa: F401
from .gate_state import BudgetInputError, build_budget_gate, check_now, load_effective_budget  # noqa: F401
from .manage import (  # noqa: F401
    BudgetManageError, BudgetUpdate, ArchiveResult,
    add_budget, archive, resume, resume_advice,
)

VERSION = "1.0.0"
__all__ = [
    "VERSION",
    "TokenMeter", "EventLogTokenMeter", "DshTokenMeter", "summarize",
    "BudgetReport", "build_report", "human_summary",
    "BudgetInputError", "build_budget_gate", "check_now", "load_effective_budget",
    "BudgetManageError", "BudgetUpdate", "ArchiveResult",
    "add_budget", "archive", "resume", "resume_advice",
]
