"""fw-budget 测试夹具：用 fw-scaffold 生成真实 v2 目录树 → runner 跑预算场景 → fw-budget 消费。

路径复用：fw-budget / fw-runner / fw-protocol / fw-scaffold（全部兄弟目录，sys.path 注入）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_FW1 = Path(__file__).resolve().parent.parent.parent
for _d in ("fw-budget", "fw-runner", "fw-protocol", "fw-scaffold"):
    _p = str((_FW1 / _d).resolve())
    if _p not in sys.path:
        sys.path.insert(0, _p)

from helpers import build_task, make_harness, module, write_task_doc  # noqa: E402,F401

# 预算测试标准任务：3 独立模块，max_parallel=1（串行可控），checkpoint_every=1
_BUDGET = {"max_tokens": 900, "warn_at": 0.7, "stop_at": 1.0}
_RUNTIME = {"max_parallel": 1, "executor_max_rounds": 8, "retry_before_switch": 2,
            "max_executor_switches": 1, "end_gate": "auto"}


@pytest.fixture
def three_root(tmp_path):
    """验收3 场景：m01/m02 完成后第 2 批 stop，m03 pending —— 加预算 resume 零重跑。"""
    mods = [module("m01", "预算甲", deps=[]), module("m02", "预算乙", deps=[]),
            module("m03", "预算丙", deps=[])]
    return build_task(tmp_path, "验收5-预算三模块", mods,
                      runtime=_RUNTIME, budget=dict(_BUDGET))


@pytest.fixture
def warn_root(tmp_path):
    """验收1 场景：3 模块两批，总消耗 800/1000=80% → warn 不停机，排行可排序。"""
    return build_task(
        tmp_path, "验收5-预警排行",
        [module("m01", "高热", deps=[]), module("m02", "中热", deps=[]),
         module("m03", "低热", deps=[])],
        runtime={"max_parallel": 2, "executor_max_rounds": 5,
                 "retry_before_switch": 2, "max_executor_switches": 1, "end_gate": "auto"},
        budget={"max_tokens": 1000, "warn_at": 0.7, "stop_at": 1.0},
    )


@pytest.fixture
def stop_root(tmp_path):
    """验收2 场景：单模块 m01 executor 消耗 600 → 全局 600/500 超 100% → 硬停。"""
    return build_task(
        tmp_path, "验收5-硬停",
        [module("m01", "超算", deps=[]), module("m02", "待跑", deps=[])],
        runtime=_RUNTIME,
        budget={"max_tokens": 500, "warn_at": 0.7, "stop_at": 1.0,
                "per_module_max_tokens": 10000},
    )


@pytest.fixture
def per_module_stop_root(tmp_path):
    """验收2 场景 B：全局不超，但 m01 单模块超过 per_module_max_tokens=500 → 硬停。"""
    return build_task(
        tmp_path, "验收5-单模块超限",
        [module("m01", "失控", deps=[]), module("m02", "幸存", deps=[])],
        runtime=_RUNTIME,
        budget={"max_tokens": 10000, "warn_at": 0.7, "stop_at": 1.0,
                "per_module_max_tokens": 500},
    )
