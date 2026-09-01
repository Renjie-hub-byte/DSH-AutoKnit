"""fw-budget 测试助手：复用 fw-runner 审计过的 helpers（build_task/module）+ 可注入 token 的 harness。

build_task 来自 fw-runner/tests/helpers.py（round_004 已审计）——写 task.yaml →
fw-scaffold 生成真实 v2 目录树。BudgetHarness 记录 executor/auditor 调用并注入 per-module tokens，
保证预算场景确定性可复现。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable, Dict, Optional

import yaml

_FW1 = Path(__file__).resolve().parent.parent.parent
import sys as _sys
for _d in ("fw-runner", "fw-protocol", "fw-scaffold"):
    _p = str((_FW1 / _d).resolve())
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

from fw_runner.drivers import AgentContext, InlineAgentDriver  # noqa: E402
from fw_runner.model import DriverOutcome  # noqa: E402
from fw_runner.review import append_done, read_review  # noqa: E402


def write_task_doc(tmp_path: Path, name: str, modules, runtime=None,
                   budget=None, integration_checks=None) -> Path:
    """与 fw-runner/tests/helpers.py 同款（本地副本，避免跨测试目录 import 抖动）。"""
    doc = {
        "task": {
            "name": name, "source_prd": "prd/x.md", "owner": "tester",
            "created": "2026-08-21", "grade": "B",
            "prediction_baseline": {"will_have": [f"{name} 产物存在"], "will_not_have": ["不做实时"]},
        },
        "budget": budget or {"max_tokens": 200000},
        "runtime": runtime or {"max_parallel": 2, "executor_max_rounds": 5,
                               "retry_before_switch": 2, "max_executor_switches": 1,
                               "end_gate": "auto"},
        "modules": modules,
        "integration": {
            "contract_file": "contracts/api.yaml",
            "check": integration_checks or {
                "dependency_cycle": True, "interface_duplicate": True,
                "acceptance_conflict": True, "prediction_baseline": True,
                "cross_module_data_dependency": True,
            },
        },
    }
    p = tmp_path / "task.yaml"
    p.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return p


def build_task(tmp_path: Path, name: str, modules, runtime=None, budget=None) -> Path:
    """写 task.yaml → fw-scaffold 生成 v2 目录树 → 返回任务根。"""
    from fw_scaffold.scaffold import generate
    yaml_path = write_task_doc(tmp_path, name, modules, runtime=runtime, budget=budget)
    res = generate(yaml_path, output_dir=tmp_path)
    return res.root


def module(id_: str, name: str, deps=None, layer: int = 1, objective: str = "目标") -> dict:
    return {
        "id": id_, "name": name, "layer": layer, "objective": objective,
        "dependencies": deps or [],
        "interfaces": [{"path": f"/api/{id_}/*", "method": ["GET"], "note": f"{id_} 接口"}],
        "acceptance": [f"{id_} 验收：按 contract.yaml 产出 src 产物"],
        "boundaries": [f"{id_} 不跨界"],
    }


class BudgetHarness:
    """可注入 per-module tokens 的 inline 驱动；记录调用序列（resume 零重跑判定用）。"""

    def __init__(self, exec_tokens: Optional[Dict[str, int]] = None,
                 audit_tokens: Optional[Dict[str, int]] = None,
                 exec_fn: Optional[Callable] = None,
                 audit_fn: Optional[Callable] = None) -> None:
        self.exec_tokens = dict(exec_tokens or {})
        self.audit_tokens = dict(audit_tokens or {})
        self.exec_fn = exec_fn
        self.audit_fn = audit_fn
        self.exec_calls: list = []
        self.audit_calls: list = []

    def make_executor(self):
        def fn(ctx: AgentContext):
            self.exec_calls.append({"mid": ctx.module.id, "round": ctx.round_no})
            if self.exec_fn is not None:
                return self.exec_fn(ctx)
            append_done(ctx.module.review_path,
                        f"exec {ctx.module.id} round {ctx.round_no} ({ctx.executor_id})")
            tokens = self.exec_tokens.get(ctx.module.id, 0)
            return DriverOutcome(status="ok", substance=True, tokens=tokens)
        return InlineAgentDriver(fn)

    def make_auditor(self):
        def fn(ctx: AgentContext):
            self.audit_calls.append({"mid": ctx.module.id, "round": ctx.round_no})
            if self.audit_fn is not None:
                return self.audit_fn(ctx)
            doc = read_review(ctx.module.review_path)
            done = [ln for ln in doc.list_done() if "（占位）" not in ln]
            tokens = self.audit_tokens.get(ctx.module.id, 0)
            if done:
                return DriverOutcome(status="ok", verdict="pass", root="", confidence=0.9,
                                     reason="外部验收自测通过", tokens=tokens)
            return DriverOutcome(status="ok", verdict="block", root="self", confidence=0.5,
                                 reason="无实质产物", blocker="缺 src/REVIEW 已做", tokens=tokens)
        return InlineAgentDriver(fn)


def make_harness(exec_tokens=None, audit_tokens=None, exec_fn=None, audit_fn=None) -> BudgetHarness:
    """便捷工厂：构造注入 per-module tokens 的 BudgetHarness。"""
    return BudgetHarness(exec_tokens=exec_tokens, audit_tokens=audit_tokens,
                         exec_fn=exec_fn, audit_fn=audit_fn)
