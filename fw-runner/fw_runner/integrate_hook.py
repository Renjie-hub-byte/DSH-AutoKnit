"""集成验收对接钩子（需求6 主体归 fw-integrate；runner 只调用钩子并处理结果）。

- runner 在所有模块完成后调用 integration_hook.run(ctx, state)
- status: passed | deferred | failed
  - failed → 运行结果 integration_failed（回人，exit 2）
  - passed/deferred → 继续（end_gate 处理）
- 结果与备注落 总日志/integration.jsonl（scaffold 已初始化该日志）
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .context import TaskContext
from .model import RunState, now_iso


@dataclass
class IntegrationReport:
    status: str                        # passed | deferred | failed
    notes: List[str] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"status": self.status, "notes": list(self.notes), "summary": dict(self.summary)}


class IntegrationHook:
    """fw-integrate 钩子协议。"""

    def run(self, ctx: TaskContext, state: RunState) -> IntegrationReport:
        raise NotImplementedError


class NullIntegrationHook(IntegrationHook):
    """默认钩子：deferred（fw-integrate 主体未实现，契约运行时校验/基线对照留该轮）。"""

    def run(self, ctx: TaskContext, state: RunState) -> IntegrationReport:
        return IntegrationReport(
            status="deferred",
            notes=["fw-integrate 主体未实现（需求6 轮）：契约运行时校验、跨模块数据依赖、"
                   "预测基线 will_have/will_not_have 对照由 fw-integrate 追加"],
            summary={"completed": list(state.completed_order), "needs_human": list(state.needs_human)},
        )


def append_integration_log(ctx: TaskContext, run_id: str, seq: int,
                           report: IntegrationReport, end_gate: str) -> Path:
    """追加一行集成日志（integration.jsonl）。"""
    line = {
        "ts": now_iso(),
        "seq": seq,
        "run_id": run_id,
        "event": "integration.check",
        "end_gate": end_gate,
        "detail": report.to_dict(),
    }
    p = ctx.integration_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
    return p
