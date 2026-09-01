"""m03 纯函数层测试：assemble/urgency/empty_result（不依赖上游 m01/m02）。

覆盖验收：
  1. 返回任务数组（assemble 输出 tasks 为数组，count 一致）
  2. 返回数组按紧急度排序（pytest 断言顺序符合预期）
  5. 任务数组每条含阶段状态与模块状态（pytest 断言字段存在）
以及确定性降级（输入不可用/ok=False/缺 runs → 空数组不抛异常）。
"""

import pytest

from dsh_task_list import (
    assemble,
    empty_result,
    urgency_of,
    URGENCY_RANK,
    URGENCY_OTHER,
)
from conftest import make_run, make_status


class TestUrgencyMapping:
    """紧急度分值：needs_human < switch < auditor < executor < 其它 < unknown。"""

    def test_rank_order(self):
        ranks = [urgency_of(s) for s in ("needs_human", "switch", "auditor", "executor")]
        assert ranks == sorted(ranks)
        assert urgency_of("needs_human") == 0
        assert urgency_of("switch") == 1
        assert urgency_of("auditor") == 2
        assert urgency_of("executor") == 3

    def test_other_passthrough_less_urgent(self):
        # 其它透传阶段（planning 等）排在 executor 之后、unknown 之前
        assert urgency_of("planning") == URGENCY_OTHER == 4

    def test_unknown_and_none_last(self):
        assert urgency_of("unknown") == 5
        assert urgency_of(None) == 5
        assert urgency_of(123) == 5

    def test_deterministic(self):
        for stage in ("needs_human", "switch", "auditor", "executor", "planning", "unknown", None):
            assert urgency_of(stage) == urgency_of(stage)


class TestAssembleReturnsTaskArray:
    """验收 1：dsh.task.list（纯函数层 assemble）返回任务数组。"""

    def test_returns_task_array(self):
        result = assemble(
            make_status(
                runs=[
                    make_run("r1", 0, "executor", module="m02"),
                    make_run("r2", 1, "auditor", module="m03"),
                ]
            )
        )
        assert result["ok"] is True
        assert isinstance(result["tasks"], list)
        assert len(result["tasks"]) == 2
        assert result["task_count"] == 2

    def test_task_array_contains_run_ids(self):
        result = assemble(
            make_status(
                runs=[
                    make_run("run-a", 0, "executor"),
                    make_run("run-b", 1, "auditor"),
                ]
            )
        )
        ids = [t["run_id"] for t in result["tasks"]]
        assert set(ids) == {"run-a", "run-b"}


class TestUrgencySorting:
    """验收 2：返回数组按紧急度排序（断言顺序符合预期）。"""

    def test_sorted_by_urgency_desc(self):
        # 构造时故意乱序：executor(3) → needs_human(0) → auditor(2) → switch(1)
        result = assemble(
            make_status(
                runs=[
                    make_run("r-exec", 0, "executor"),
                    make_run("r-human", 1, "needs_human"),
                    make_run("r-audit", 2, "auditor"),
                    make_run("r-switch", 3, "switch"),
                ]
            )
        )
        assert [t["stage"] for t in result["tasks"]] == [
            "needs_human",
            "switch",
            "auditor",
            "executor",
        ]

    def test_other_and_unknown_after_executor(self):
        result = assemble(
            make_status(
                runs=[
                    make_run("r-unknown", 0, "unknown"),
                    make_run("r-planning", 1, "planning"),
                    make_run("r-exec", 2, "executor"),
                ]
            )
        )
        assert [t["stage"] for t in result["tasks"]] == ["executor", "planning", "unknown"]

    def test_same_urgency_stable_by_index(self):
        # 同紧急度按 index（原始快照序）升序 → 确定性
        result = assemble(
            make_status(
                runs=[
                    make_run("r-a", 0, "auditor"),
                    make_run("r-c", 2, "auditor"),
                    make_run("r-b", 1, "auditor"),
                ]
            )
        )
        assert [t["index"] for t in result["tasks"]] == [0, 1, 2]

    def test_deterministic_repeat_call(self):
        runs = [
            make_run("r-exec", 0, "executor"),
            make_run("r-human", 1, "needs_human"),
            make_run("r-audit", 2, "auditor"),
        ]
        r1 = assemble(make_status(runs=runs))
        r2 = assemble(make_status(runs=runs))
        assert r1 == r2  # 同输入多次调用结果精确相等


class TestFieldExistence:
    """验收 5：任务数组每条含阶段状态与模块状态（断言字段存在）。"""

    REQUIRED_FIELDS = [
        "run_id", "index", "phase", "status", "module", "updated_at",  # 原始信息
        "stage", "stage_label",                                          # 阶段状态（键/标签）
        "executor_running", "auditor_reviewing",                         # 阶段状态（布尔）
        "module_states",                                                 # 模块状态（状态表）
        "urgency",                                                       # 紧急度分值
    ]

    def test_each_entry_has_stage_and_module_status(self):
        result = assemble(
            make_status(
                runs=[
                    make_run("r1", 0, "executor", module="m02", module_states={"m01": "done"}),
                    make_run("r2", 1, "auditor", module="m03"),
                ]
            )
        )
        assert result["task_count"] > 0
        for entry in result["tasks"]:
            for key in self.REQUIRED_FIELDS:
                assert key in entry, "任务条目缺字段: %s" % key

    def test_stage_and_module_values(self):
        result = assemble(
            make_status(
                runs=[
                    make_run("r1", 0, "executor", module="m02"),
                ]
            )
        )
        entry = result["tasks"][0]
        assert entry["stage"] == "executor"
        assert entry["executor_running"] is True
        assert entry["module"] == "m02"

    def test_m02b_extra_fields_passthrough(self):
        # 前向兼容：m02b（换人/needs_human）扩展字段原样透传
        result = assemble(
            make_status(
                runs=[
                    make_run("r1", 0, "switch", switch_in_progress=True, needs_human=False),
                ]
            )
        )
        entry = result["tasks"][0]
        assert entry["switch_in_progress"] is True
        assert entry["needs_human"] is False


class TestAssembleDegradation:
    """确定性降级：输入不可用 → 空数组，不抛异常。"""

    def test_none_input(self):
        result = assemble(None)
        assert result["ok"] is False
        assert result["tasks"] == []
        assert result["task_count"] == 0

    def test_non_dict_input(self):
        assert assemble("nope")["tasks"] == []
        assert assemble(42)["ok"] is False

    def test_ok_false_input(self):
        result = assemble(make_status(ok=False, task_dir="/missing"))
        assert result["ok"] is False
        assert result["tasks"] == []
        assert result["task_dir"] == "/missing"

    def test_missing_runs_key(self):
        result = assemble({"ok": True, "task_dir": "/x"})
        assert result["ok"] is False
        assert result["tasks"] == []

    def test_empty_runs_is_valid_empty(self):
        # 无活跃 run：有效输入 + 空 runs → ok=True, 空数组（不抛异常）
        result = assemble(make_status(ok=True, runs=[]))
        assert result["ok"] is True
        assert result["tasks"] == []
        assert result["task_count"] == 0

    def test_non_dict_run_skipped_with_error(self):
        result = assemble(make_status(runs=[make_run("r1", 0, "executor"), "bad", None]))
        assert result["ok"] is True
        assert [t["run_id"] for t in result["tasks"]] == ["r1"]
        assert any("非 dict" in e for e in result["errors"])

    def test_empty_result_never_raises(self):
        assert empty_result("/missing") == {
            "ok": False,
            "task_dir": "/missing",
            "tasks": [],
            "task_count": 0,
            "errors": [],
        }
