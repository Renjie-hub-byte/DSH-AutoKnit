"""集成测试：真实临时任务目录 → dsh.task.list 全链路（m01 fwr.dir.read → m02 fwr.status.compute → m03 组装排序）。

覆盖验收（pytest 注入临时任务目录）：
  1. dsh.task.list 返回任务数组
  2. 返回数组按紧急度排序（断言顺序符合预期）
  3. 任务目录缺失 → 确定性空降级（空数组）且不抛异常
  4. 无活跃 run → 确定性空降级（空数组）且不抛异常
  5. 任务数组每条含阶段状态与模块状态（断言字段存在）

上游 m01/m02 已交付（dependency 边 m01→m02→m03），conftest 已挂载其 src；
若不可用则本文件整体 skip（纯函数层单测不受影响）。
"""

import pytest

from conftest import AUDITOR_SNAP, EXECUTOR_SNAP, UPSTREAM_OK

pytestmark = pytest.mark.skipif(
    not UPSTREAM_OK,
    reason="上游 m01/m02 src 不可用，跳过集成测试（纯函数层单测不受影响）",
)

from dsh_task_list import dsh  # noqa: E402  上游可用后才导入（仅依赖本模块自身）


class TestFullChain:
    """验收 1 + 2 + 5：注入临时任务目录，全链路返回排序任务数组。"""

    def test_returns_task_array(self, build_task_dir):
        """验收 1：dsh.task.list 返回任务数组（task_count 与 run 数一致）。"""
        root = build_task_dir(runs=[EXECUTOR_SNAP, AUDITOR_SNAP])
        result = dsh.task.list(str(root))
        assert result["ok"] is True
        assert isinstance(result["tasks"], list)
        assert result["task_count"] == 2
        assert len(result["tasks"]) == 2

    def test_sorted_by_urgency(self, build_task_dir):
        """验收 2：auditor(2) 应排在 executor(3) 之前（紧急度升序）。"""
        root = build_task_dir(runs=[EXECUTOR_SNAP, AUDITOR_SNAP])  # 快照序 executor 在前
        result = dsh.task.list(str(root))
        assert [t["stage"] for t in result["tasks"]] == ["auditor", "executor"]
        assert result["tasks"][0]["run_id"] == AUDITOR_SNAP["run_id"]

    def test_each_entry_has_stage_and_module_status(self, build_task_dir):
        """验收 5：每条含阶段状态与模块状态（字段存在且值正确）。"""
        root = build_task_dir(runs=[EXECUTOR_SNAP, AUDITOR_SNAP])
        result = dsh.task.list(str(root))
        by_id = {t["run_id"]: t for t in result["tasks"]}

        ex = by_id[EXECUTOR_SNAP["run_id"]]
        assert ex["stage"] == "executor"
        assert ex["stage_label"] == "executor 执行中"
        assert ex["executor_running"] is True
        assert ex["module"] == "m02"
        assert "module_states" in ex

        au = by_id[AUDITOR_SNAP["run_id"]]
        assert au["stage"] == "auditor"
        assert au["stage_label"] == "auditor 验收中"
        assert au["auditor_reviewing"] is True
        assert au["module"] == "m03"
        assert "module_states" in au

    def test_task_name_injected_from_task_yaml(self, build_task_dir):
        """完整链路注入 task.yaml 的任务名（确定性）。"""
        root = build_task_dir(runs=[EXECUTOR_SNAP])
        result = dsh.task.list(str(root))
        assert result["tasks"][0]["task_name"] == "dsh_cockpit_m01_split验证"


class TestDegradedTaskDirMissing:
    """验收 3：任务目录缺失 → 确定性空降级（空数组）且不抛异常。"""

    def test_nonexistent_dir(self, tmp_path):
        result = dsh.task.list(str(tmp_path / "no-such-dir"))
        assert result["ok"] is False
        assert result["tasks"] == []
        assert result["task_count"] == 0

    def test_dir_without_task_yaml(self, build_task_dir):
        root = build_task_dir(with_task_yaml=False)
        result = dsh.task.list(str(root))
        assert result["ok"] is False
        assert result["tasks"] == []

    def test_deterministic_repeat_call(self, tmp_path):
        missing = str(tmp_path / "no-such-dir")
        assert dsh.task.list(missing) == dsh.task.list(missing)


class TestNoActiveRun:
    """验收 4：无活跃 run → 确定性空降级（空数组）且不抛异常。"""

    def test_empty_snapshot_runs(self, build_task_dir):
        # snapshot.json 存在但 runs 为空
        root = build_task_dir(runs=[])
        result = dsh.task.list(str(root))
        assert result["ok"] is True
        assert result["tasks"] == []
        assert result["task_count"] == 0

    def test_snapshot_file_missing(self, build_task_dir):
        # snapshot.json 缺失 → m01 记 missing，runs 为空
        root = build_task_dir(with_snapshot=False)
        result = dsh.task.list(str(root))
        assert result["ok"] is True
        assert result["tasks"] == []

    def test_deterministic_repeat_call(self, build_task_dir):
        root = build_task_dir(runs=[])
        assert dsh.task.list(str(root)) == dsh.task.list(str(root))
