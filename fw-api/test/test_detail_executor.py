"""验收 1+2：pytest 注入任务目录，断言 dsh.task.detail 返回指定 run 的模块级状态
（executor 执行中场景）。

对应任务书-m04.yaml acceptance：
  1) pytest 注入任务目录断言 dsh.task.detail 返回指定 run 的模块级状态
  2) 覆盖 executor 执行中场景（pytest 断言）
"""

import pytest

import fwr_detail  # noqa: F401  （包导入冒烟）
from fwr_detail import dsh, detail


class TestDetailExecutorRunning:
    def test_detail_returns_executor_run_module_status(self, build_task_dir):
        """注入任务目录 → dsh.task.detail 返回指定 run（executor 执行中）的模块级状态。"""
        task_dir = build_task_dir()
        res = dsh.task.detail(str(task_dir), "run-20260823-001")

        assert res["ok"] is True
        assert res["found"] is True
        assert res["task_dir"] == str(task_dir)
        assert res["run_id"] == "run-20260823-001"
        assert res["task_name"] == "dsh_cockpit_m01_split验证"

        run = res["run"]
        assert run["run_id"] == "run-20260823-001"
        assert run["phase"] == "executor"
        assert run["status"] == "running"
        assert run["stage"] == "executor"
        assert run["stage_label"] == "executor 执行中"
        assert run["executor_running"] is True
        assert run["auditor_reviewing"] is False

    def test_detail_returns_module_states_table(self, build_task_dir):
        """模块级状态表透传：snapshot.modules dict 原样返回（模块级状态是本服务核心）。"""
        task_dir = build_task_dir()
        run = detail(str(task_dir), "run-20260823-001")["run"]

        assert run["module_states"] == {
            "m01": {"stage": "done", "note": "任务目录解析完成"},
            "m02": {"stage": "executor 执行中", "executor_round": 2},
        }
        assert run["module"] == "m02"

    def test_detail_selects_specified_run_only(self, build_task_dir):
        """多 run 任务目录：只返回指定 run 的详情，不含其它 run。"""
        task_dir = build_task_dir()
        res = detail(str(task_dir), "run-20260823-001")

        assert res["run"]["run_id"] == "run-20260823-001"
        assert res["run"]["stage"] == "executor"
        # run-20260823-002 是 auditor，不得混入
        assert res["run"]["phase"] == "executor"

    def test_detail_executor_status_wordlist(self, build_task_dir):
        """执行中词表变体（working/in_progress/executing/started）均判为 executor 执行中。"""
        for status in ("working", "in_progress", "executing", "started"):
            runs = [{
                "run_id": "run-w", "phase": "executor", "status": status, "module": "m02",
            }]
            task_dir = build_task_dir(snapshot_runs=runs)
            run = detail(str(task_dir), "run-w")["run"]
            assert run["stage"] == "executor", status
            assert run["executor_running"] is True, status
            assert run["stage_label"] == "executor 执行中", status

    def test_detail_status_missing_still_executor_running(self, build_task_dir):
        """executor 阶段且 status 缺省 → 仍判定为执行中（执行中常态表述，上游 m02b 语义）。"""
        runs = [{
            "run_id": "run-x", "phase": "executor", "module": "m02",
        }]
        task_dir = build_task_dir(snapshot_runs=runs)
        run = detail(str(task_dir), "run-x")["run"]
        assert run["stage"] == "executor"
        assert run["executor_running"] is True
        assert run["status"] is None

    def test_detail_metadata_passthrough(self, build_task_dir):
        """当前模块/更新时间/index 透传（模块级状态查询的元信息完整性）。"""
        task_dir = build_task_dir()
        run = detail(str(task_dir), "run-20260823-001")["run"]
        assert run["module"] == "m02"
        assert run["updated_at"] == "2026-08-23T22:16:34+08:00"
        assert run["index"] == 0

    def test_detail_module_fallback_from_latest_dispatch_event(self, build_task_dir):
        """snapshot 无 module 时兜底取最近一次派发事件的 module（上游 m02b 语义）。"""
        runs = [{
            "run_id": "run-001", "phase": "executor", "status": "running",
        }]
        events = [
            {"ts": "t1", "run_id": "run-001", "event": "dispatch", "to": "executor", "module": "m01"},
            {"ts": "t2", "run_id": "run-001", "event": "dispatch", "to": "executor", "module": "m02"},
            {"ts": "t3", "run_id": "other-run", "event": "dispatch", "module": "m99"},
        ]
        task_dir = build_task_dir(snapshot_runs=runs, dispatch_events=events)
        run = detail(str(task_dir), "run-001")["run"]
        assert run["module"] == "m02"  # 逆序最近一次且 run_id 匹配

    def test_namespace_alias_equals_top_level(self, build_task_dir):
        """契约命名空间 dsh.task.detail 与顶层 detail 结果一致。"""
        task_dir = build_task_dir()
        assert dsh.task.detail(str(task_dir), "run-20260823-001") == \
            detail(str(task_dir), "run-20260823-001")


class TestDeterminism:
    def test_same_input_same_output(self, build_task_dir):
        """同一任务目录同一 run 两次调用结果精确相等（确定性）。"""
        task_dir = build_task_dir()
        assert detail(str(task_dir), "run-20260823-001") == \
            detail(str(task_dir), "run-20260823-001")

    def test_output_dict_key_order_stable(self, build_task_dir):
        """输出 dict 键顺序固定（确定性结构化）。"""
        task_dir = build_task_dir()
        res = detail(str(task_dir), "run-20260823-001")
        assert list(res.keys()) == [
            "ok", "task_dir", "run_id", "found", "task_name", "run", "errors",
        ]
        assert list(res["run"].keys()) == [
            "run_id", "index", "phase", "status", "module", "updated_at",
            "stage", "stage_label", "executor_running", "auditor_reviewing",
            "switch_in_progress", "needs_human", "module_states",
        ]
