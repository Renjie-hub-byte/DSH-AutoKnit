"""上下文加载 + 调度规划（纯逻辑）：复用 fw-protocol effective + fw-scaffold 目录。"""
from __future__ import annotations

import pytest

from fw_runner.context import RunnerInputError, load_task_context
from fw_runner.scheduler import CycleError, plan_batches, topological_layers


def test_context_loads_effective_and_dirs(indep4_root):
    ctx = load_task_context(indep4_root)
    assert ctx.task_name == "验收1-四独立"
    assert ctx.module_order == ["m01", "m02", "m03", "m04"]
    assert ctx.dependencies == {m: [] for m in ctx.module_order}
    assert ctx.config.max_parallel == 3          # 任务书 runtime 覆盖默认 3
    assert ctx.config.retry_before_switch == 2
    assert ctx.config.max_executor_switches == 1
    # fw-scaffold 产物形状
    mdir = ctx.modules["m01"].dir
    assert mdir.name.startswith("m01-")
    assert ctx.modules["m01"].review_path.is_file()
    assert ctx.modules["m01"].contract_path.is_file()
    assert ctx.modules["m01"].book_path.is_file()
    assert ctx.modules["m01"].delivery_path.is_file()


def test_context_missing_modules_dir(tmp_path):
    """缺 scaffold 目录 → RunnerInputError。"""
    p = tmp_path / "x"
    p.mkdir()
    (p / "task.yaml").write_text("task: {name: x}\nmodules: [{id: m01}]\n", encoding="utf-8")
    with pytest.raises(RunnerInputError):
        load_task_context(p)


def test_cost_first_mode_caps_parallel(indep4_root):
    ctx = load_task_context(indep4_root, mode="cost_first")
    assert ctx.config.max_parallel == min(3, 2)
    assert ctx.config.retry_before_switch == 3   # cost_first 提升同 executor 耐心


def test_cli_override_beats_mode(indep4_root):
    ctx = load_task_context(indep4_root, mode="cost_first",
                            overrides={"max_parallel": 4})
    assert ctx.config.max_parallel == 4
    assert ctx.config.overrides["max_parallel"] == 4


def test_scheduler_layers_and_batches():
    diamond = [{"id": "a", "dependencies": []},
               {"id": "b", "dependencies": ["a"]},
               {"id": "c", "dependencies": ["a"]},
               {"id": "d", "dependencies": ["b", "c"]}]
    assert topological_layers(diamond) == [["a"], ["b", "c"], ["d"]]
    assert plan_batches(diamond, 2) == [["a"], ["b", "c"], ["d"]]
    assert plan_batches(diamond, 4) == [["a"], ["b", "c"], ["d"]]
    assert plan_batches(diamond, 1) == [["a"], ["b"], ["c"], ["d"]]


def test_scheduler_cycle_rejected():
    with pytest.raises(CycleError):
        plan_batches([{"id": "x", "dependencies": ["y"]},
                      {"id": "y", "dependencies": ["x"]}], 2)
