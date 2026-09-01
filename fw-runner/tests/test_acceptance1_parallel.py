"""需求4 验收 1：4 独立模块 + max_parallel=3 → 3 个并行 + 1 个排队。

机器可复现断言：
- 观测最大并发 == 3（绝不 4）——批次1 三个 worker 同刻运行
- m04（第 4 个）的开始时间 ≥ 批次1 全部结束（排队：批次间串行）
- 调度批次结构 == [[m01,m02,m03],[m04]]
- 全部通过 → status=complete，模块全 done
"""
from __future__ import annotations

import time

from fw_runner.runner import run


def test_4_independent_max_parallel_3(indep4_root, harness):
    """验收 1：3 并行 + 1 排队。"""
    exec_driver = harness.make_executor()
    aud_driver = harness.make_auditor()

    result = run(indep4_root, executor_driver=exec_driver, auditor_driver=aud_driver)

    assert result.status == "complete", result.to_dict()
    assert result.exit_reason == "all_modules_done"
    assert sorted(result.completed) == ["m01", "m02", "m03", "m04"]
    assert all(result.modules[m]["status"] == "done" for m in ("m01", "m02", "m03", "m04"))

    # 1) 观测最大并发 == 3（线程安全性由 Harness 锁保证）
    assert harness.max_active == 3, f"期望 3 并行，观测 {harness.max_active}（绝不为 4）"

    # 2) 排队：m04 必须在批次1（m01..m03）全部结束后才启动
    batch1_end = max(harness.exec_end_times[m] for m in ("m01", "m02", "m03"))
    m04_start = harness.exec_start_times["m04"]
    assert m04_start >= batch1_end, f"m04 排队失败：start={m04_start} < batch1_end={batch1_end}"

    # 3) 真实并行证据：批次1 三个模块的开始时刻集中（同一批并发启动）
    starts = sorted(harness.exec_start_times[m] for m in ("m01", "m02", "m03"))
    spread = starts[-1] - starts[0]
    # 真实并行证据由 max_active==3（同时活跃)保证；散布阈值放宽到 0.5s，避免在负载较高的审计机上偶发误报
    assert spread < 0.5, f"批次1 并发启动散布过大: {spread}"

    # 4) 每个模块恰好跑 1 轮 executor + 1 轮 auditor
    assert len(harness.exec_calls) == 4
    assert len(harness.audit_calls) == 4


def test_batches_shape_indep4():
    """调度批次结构直接断言（纯函数）。"""
    from fw_runner.scheduler import plan_batches
    mods = [{"id": f"m0{i}", "dependencies": []} for i in range(1, 5)]
    assert plan_batches(mods, 3) == [["m01", "m02", "m03"], ["m04"]]
