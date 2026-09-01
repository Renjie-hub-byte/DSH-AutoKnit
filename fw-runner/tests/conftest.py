"""fw-runner 测试夹具：用 fw-scaffold 生成真实 v2 目录树 → runner 消费真实产物形状。

依赖：fw-scaffold（round_002 已审计）生成目录；fw-protocol 校验。全部为框架内复用。
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest
import yaml

_FW1 = Path(__file__).resolve().parent.parent.parent
for _d in ("fw-runner", "fw-protocol", "fw-scaffold"):
    _p = str((_FW1 / _d).resolve())
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fw_runner.context import load_task_context  # noqa: E402
from fw_runner.drivers import AgentContext, InlineAgentDriver  # noqa: E402
from fw_runner.model import DriverOutcome  # noqa: E402
from fw_runner.review import append_done, read_review  # noqa: E402


from helpers import build_task, module, write_task_doc  # noqa: F401


@pytest.fixture(autouse=True)
def _isolate_registry(tmp_path, monkeypatch):
    """隔离注册表：任何 run() 调用都会写 runs.json，必须指向临时路径避免污染真实 ~/.autoknit/runs.json。"""
    monkeypatch.setenv("AUTOKNIT_RUNS_REGISTRY", str(tmp_path / ".registry" / "runs.json"))
    yield


@pytest.fixture
def indep4_root(tmp_path):
    """验收1：4 个独立模块（无依赖）。"""
    mods = [module(f"m0{i}", f"独立模块{i}", deps=[]) for i in range(1, 5)]
    return build_task(tmp_path, "验收1-四独立", mods, runtime={"max_parallel": 3,
                                                             "executor_max_rounds": 8,
                                                             "retry_before_switch": 2,
                                                             "max_executor_switches": 1,
                                                             "end_gate": "auto"})


@pytest.fixture
def chain_root(tmp_path):
    """验收2：依赖链 m01→m02（A→D 的最短链）；max_parallel=3。"""
    mods = [module("m01", "A-上游", deps=[]), module("m02", "D-下游", deps=["m01"])]
    return build_task(tmp_path, "验收2-依赖链", mods, runtime={"max_parallel": 3,
                                                            "executor_max_rounds": 8,
                                                            "retry_before_switch": 2,
                                                            "max_executor_switches": 1,
                                                            "end_gate": "auto"})


@pytest.fixture
def single_root(tmp_path):
    """验收3：单模块（升级链/心跳/根因测试用）。"""
    return build_task(tmp_path, "验收3-单模块", [module("m01", "升级链样本", deps=[])])


@pytest.fixture
def resume_root(tmp_path):
    """验收4：3 模块（checkpoint_every=1）。"""
    mods = [module("m01", "步骤乙", deps=[]), module("m02", "步骤丙", deps=[]),
            module("m03", "步骤丁", deps=[])]
    return build_task(tmp_path, "验收4-中断续跑", mods, runtime={"max_parallel": 2,
                                                             "executor_max_rounds": 10,
                                                             "retry_before_switch": 2,
                                                             "max_executor_switches": 1,
                                                             "end_gate": "auto"})


class Harness:
    """可编程 inline 驱动：记录调用/并发度/时间，向量化行为（确定性复现调度）。"""

    def __init__(self, exec_fn=None, audit_fn=None):
        self.exec_calls: list[dict] = []
        self.audit_calls: list[dict] = []
        self.exec_fn = exec_fn
        self.audit_fn = audit_fn
        self._active = 0
        self.max_active = 0
        self._lock = threading.Lock()
        self.exec_start_times: dict[str, float] = {}
        self.exec_end_times: dict[str, float] = {}

    def make_executor(self, default=None):
        def fn(ctx: AgentContext):
            t0 = time.monotonic()
            with self._lock:
                self._active += 1
                self.max_active = max(self.max_active, self._active)
                call = {"mid": ctx.module.id, "round": ctx.round_no,
                        "executor_id": ctx.executor_id, "t0": t0}
                self.exec_calls.append(call)
                self.exec_start_times.setdefault(ctx.module.id, t0)
            try:
                if self.exec_fn is not None:
                    return self.exec_fn(ctx)
                if default is not None:
                    return default(ctx)
                return self._default_executor(ctx)
            finally:
                with self._lock:
                    self._active -= 1
                    self.exec_end_times[ctx.module.id] = time.monotonic()

        return InlineAgentDriver(fn)

    def make_auditor(self, default=None):
        def fn(ctx: AgentContext):
            with self._lock:
                self.audit_calls.append({"mid": ctx.module.id, "round": ctx.round_no,
                                         "executor_id": ctx.executor_id})
            if self.audit_fn is not None:
                return self.audit_fn(ctx)
            if default is not None:
                return default(ctx)
            return self._default_auditor(ctx)

        return InlineAgentDriver(fn)

    def _default_executor(self, ctx: AgentContext):
        # 真实文件效应：写 REVIEW 已做 + src 产物（substance 可由指纹也判定为 True）
        append_done(ctx.module.review_path,
                    f"exec {ctx.module.id} round {ctx.round_no} ({ctx.executor_id})")
        out = ctx.module.dir / "src" / f"out-{ctx.round_no}.txt"
        out.write_text(f"artifact from {ctx.executor_id} round {ctx.round_no}\n", encoding="utf-8")
        return DriverOutcome(status="ok", substance=True, tokens=0)

    def _default_auditor(self, ctx: AgentContext):
        doc = read_review(ctx.module.review_path)
        done = len([ln for ln in doc.list_done() if "（占位）" not in ln])
        if done >= 1:
            return DriverOutcome(status="ok", verdict="pass", evidence_level="L2", root="", confidence=0.9,
                                 reason="外部验收自测通过（真实文件核对）")
        return DriverOutcome(status="ok", verdict="block", root="self", confidence=0.5,
                             reason="无实质产物", blocker="缺 src/REVIEW 已做")


@pytest.fixture
def harness():
    return Harness()

