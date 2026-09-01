"""需求4 验收 2：依赖链 A→D → D 等 A 完成才启动。

机器可复现断言（max_parallel=3 下）：
- 调度批次 == [[m01],[m02]]（链不并行）
- m02(D) 开始时间 ≥ m01(A) 结束时间
- D 的 executor 开工时看到 A 已完成（tmp/m01.done 标记 + REVIEW 状态）
"""
from __future__ import annotations

import time
from pathlib import Path

from fw_runner.model import DriverOutcome
from fw_runner.runner import run


class _ChainHarness:
    """链式验收：为每个模块在通过后写 tmp/{mid}.done 标记；下游开工断言上游已 done。"""

    def __init__(self):
        self.calls: list[tuple[str, int]] = []
        self.start: dict[str, float] = {}
        self.end: dict[str, float] = {}
        self.dep_markers_seen: dict[str, list[str]] = {}
        self.marker_files: dict[str, Path] = {}

    def build(self):
        from fw_runner.drivers import InlineAgentDriver

        def executor(ctx):
            mid, rnd, mdir = ctx.module.id, ctx.round_no, ctx.module.dir
            self.calls.append((mid, rnd))
            self.start.setdefault(mid, time.monotonic())
            # 下游开工断言：所有上游标记已存在（依赖已完成；标记写 shared/ 跨模块可见）
            shared = ctx.task_root / "shared"
            seen = []
            for dep in ctx.module.dependencies:
                m = shared / f"done-{dep}"
                seen.append((dep, m.exists()))
                assert m.exists(), f"{mid} 开工时上游 {dep} 尚未完成！"
            self.dep_markers_seen.setdefault(mid, []).extend(seen)
            # 干活
            from fw_runner.review import append_done
            append_done(mdir / "REVIEW.md", f"{mid} round {rnd}")
            (mdir / "src" / f"out-{rnd}.txt").write_text("x\n", encoding="utf-8")
            return DriverOutcome(status="ok", substance=True)

        def auditor(ctx):
            shared = ctx.task_root / "shared"
            shared.mkdir(exist_ok=True)
            (shared / f"done-{ctx.module.id}").write_text(
                f"done at {time.monotonic()}\n", encoding="utf-8")
            self.end[ctx.module.id] = time.monotonic()
            return DriverOutcome(status="ok", verdict="pass", evidence_level="L2", root="", confidence=0.9,
                                 reason="上游依赖核对通过")

        return InlineAgentDriver(executor), InlineAgentDriver(auditor)


def test_dependency_chain_A_waits_D(chain_root):
    """验收 2：A→D，D 等 A 完成才启动（max_parallel=3 也不并行）。"""
    h = _ChainHarness()
    exec_driver, aud_driver = h.build()

    result = run(chain_root, executor_driver=exec_driver, auditor_driver=aud_driver)

    assert result.status == "complete"
    assert result.completed == ["m01", "m02"]
    # 时间序：D(m02) 开始 ≥ A(m01) 结束
    assert h.start["m02"] >= h.end["m01"], "D 未等 A 完成就启动"
    # D 开工时确认上游标记存在（断言在 executor 内部已执行）
    assert ("m01", True) in h.dep_markers_seen.get("m02", [])
    # 调用序：A 先于 D
    assert h.calls == [("m01", 1), ("m02", 1)]


def test_chain_batches_shape():
    """调度批次：链在 max_parallel=3 下也不并行。"""
    from fw_runner.scheduler import plan_batches
    chain = [{"id": "A", "dependencies": []}, {"id": "D", "dependencies": ["A"]}]
    assert plan_batches(chain, 3) == [["A"], ["D"]]
