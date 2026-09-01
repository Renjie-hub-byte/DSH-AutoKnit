"""fw-protocol 结构化校验结果模型。

Issue       —— 单条校验发现问题（严重级：error / conflict / warning）
ValidationResult —— 一次校验的完整结果：
    - ok        : 无 error（conflict 不算 error，conflict 需人工定优先级）
    - status    : "pass"（通过）| "conflict"（需人工定优先级）| "error"（校验失败）
    - effective : 套用默认值后的任务书深拷贝（供 scaffold/runner/integrate 直接消费）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class Issue:
    """单条校验问题。severity: error | conflict | warning。"""

    code: str
    severity: str
    message: str
    module_id: Optional[str] = None
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
        }
        if self.module_id is not None:
            d["module_id"] = self.module_id
        if self.detail:
            d["detail"] = self.detail
        return d


@dataclass(frozen=True)
class ValidationResult:
    """一次 task.yaml 校验的结果。"""

    errors: Tuple[Issue, ...] = ()
    conflicts: Tuple[Issue, ...] = ()
    warnings: Tuple[Issue, ...] = ()
    # 套用默认值后的任务书（深拷贝）。即使有 error，也尽可能给出（供下游参考，勿直接执行）。
    effective: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0

    @property
    def status(self) -> str:
        if self.errors:
            return "error"
        if self.conflicts:
            return "conflict"
        return "pass"

    @property
    def all_issues(self) -> Tuple[Issue, ...]:
        return self.errors + self.conflicts + self.warnings

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status,
            "errors": [i.to_dict() for i in self.errors],
            "conflicts": [i.to_dict() for i in self.conflicts],
            "warnings": [i.to_dict() for i in self.warnings],
            "effective": self.effective,
        }
