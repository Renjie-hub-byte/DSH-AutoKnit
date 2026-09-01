"""验收 3：覆盖 auditor 验收中场景（pytest 断言）。

对应任务书-m04.yaml acceptance 第 3 条（objective 第一步的后半）。
"""

from fwr_detail import detail


class TestDetailAuditorReviewing:
    def test_detail_returns_auditor_run_module_status(self, build_task_dir):
        """注入任务目录 → dsh.task.detail 返回 auditor 验收中 run 的模块级状态。"""
        task_dir = build_task_dir()
        res = detail(str(task_dir), "run-20260823-002")

        assert res["ok"] is True
        assert res["found"] is True
        assert res["run_id"] == "run-20260823-002"

        run = res["run"]
        assert run["phase"] == "auditor"
        assert run["status"] == "reviewing"
        assert run["stage"] == "auditor"
        assert run["stage_label"] == "auditor 验收中"
        assert run["auditor_reviewing"] is True
        assert run["executor_running"] is False

    def test_detail_auditor_module_states_table(self, build_task_dir):
        """auditor 场景模块级状态表透传：snapshot.modules dict 原样返回。"""
        task_dir = build_task_dir()
        run = detail(str(task_dir), "run-20260823-002")["run"]
        assert run["module_states"] == {
            "m03": {"stage": "auditor 验收中", "auditor_round": 1},
        }
        assert run["module"] == "m03"

    def test_detail_auditor_status_wordlist(self, build_task_dir):
        """验收中词表变体（checking/accepting/running/started）均判为 auditor 验收中。"""
        for status in ("checking", "accepting", "running", "started"):
            runs = [{
                "run_id": "run-a", "phase": "auditor", "status": status, "module": "m03",
            }]
            task_dir = build_task_dir(snapshot_runs=runs)
            run = detail(str(task_dir), "run-a")["run"]
            assert run["stage"] == "auditor", status
            assert run["auditor_reviewing"] is True, status
            assert run["stage_label"] == "auditor 验收中", status

    def test_detail_auditor_status_missing_still_reviewing(self, build_task_dir):
        """auditor 阶段且 status 缺省 → 仍判定为验收中（验收中常态表述，上游 m02b 语义）。"""
        runs = [{
            "run_id": "run-b", "phase": "auditor", "module": "m03",
        }]
        task_dir = build_task_dir(snapshot_runs=runs)
        run = detail(str(task_dir), "run-b")["run"]
        assert run["stage"] == "auditor"
        assert run["auditor_reviewing"] is True
        assert run["status"] is None

    def test_detail_executor_run_not_marked_auditor(self, build_task_dir):
        """反向：executor 执行中的 run 不得被误判为 auditor 验收中。"""
        task_dir = build_task_dir()
        run = detail(str(task_dir), "run-20260823-001")["run"]
        assert run["auditor_reviewing"] is False
        assert run["stage"] == "executor"
