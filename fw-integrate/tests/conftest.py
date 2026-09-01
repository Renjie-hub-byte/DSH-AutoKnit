"""fw-integrate 测试夹具：真实 fw-scaffold 目录树 + 真实 fw-runner 产物（集成消费真实形态）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_FW1 = Path(__file__).resolve().parent.parent.parent
for _d in ("fw-integrate", "fw-scaffold", "fw-protocol", "fw-runner", "fw-budget"):
    _p = str((_FW1 / _d).resolve())
    if _p not in sys.path:
        sys.path.insert(0, _p)

from helpers import (  # noqa: F401
    DELIVERY_EVIDENCE, PRODUCER_ARTIFACTS, build_task, conforming_auditor,
    conforming_executor, module, module_dir, run_runner_inline, write_task_doc,
)


@pytest.fixture
def conform_root(tmp_path):
    """验收3：3 模块依赖链 m01→m02→m03，真实 runner 完整跑一遍（全部交付）→ 任务根。"""
    import json
    mods = [module("m01", "数据采集", deps=[],
                   interfaces=[{"path": "/api/order/*", "method": ["POST", "PUT"],
                                "note": "订单写入"}],
                   objective="读取原始订单 CSV 并落盘为 JSON"),
            module("m02", "数据清洗", deps=["m01"],
                   interfaces=[{"path": "/api/order/*", "method": ["GET"],
                                "note": "清洗后订单查询"}],
                   objective="对采集到的订单做标准化清洗与字段校验"),
            module("m03", "报表输出", deps=["m02"],
                   interfaces=[{"path": "/api/report/*", "method": ["POST"],
                                "note": "报表生成"}],
                   objective="输出按日聚合的订单统计 CSV")]
    root = build_task(tmp_path, "验收3-完整交付", mods,
                      runtime={"max_parallel": 2, "executor_max_rounds": 5,
                               "retry_before_switch": 2, "max_executor_switches": 1,
                               "end_gate": "auto"})
    # 契约区与 read_api 在 scaffold 已按 interfaces 生成（comfy 一致）
    result = run_runner_inline(root, conforming_executor(root))
    assert result.status == "complete", f"runner 应 complete，实为 {result.status}"
    return root


@pytest.fixture
def demo_root(tmp_path):
    """示例订单管道（演示 executor，不交付契约产物）—— 基线缺失场景。"""
    mods = [module("m01", "数据采集", deps=[],
                   interfaces=[{"path": "/api/order/*", "method": ["POST", "PUT"],
                                "note": "订单写入接口（数据源侧声明）"}]),
            module("m02", "数据清洗", deps=["m01"],
                   interfaces=[{"path": "/api/order/*", "method": ["GET"],
                                "note": "清洗后的订单查询接口"}],
                   objective="对采集到的订单做标准化清洗与字段校验"),
            module("m03", "报表输出", deps=["m02"],
                   interfaces=[{"path": "/api/report/*", "method": ["POST"],
                                "note": "报表生成接口"}],
                   objective="输出按日聚合的订单统计 CSV")]
    return build_task(tmp_path, "示例-订单管道", mods, runtime={"max_parallel": 2})
