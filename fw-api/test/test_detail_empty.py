"""验收 6：run 不存在时返回确定性空结果且不抛异常（pytest 断言）。

对应任务书-m04.yaml acceptance 第 6 条（objective 的最后一步——实现已随服务
正确性内建，本轮一并断言；确定性空结构逐字段验证）。
"""

import pytest

from fwr_detail import detail, empty_result


class TestRunNotFound:
    def test_run_not_found_returns_deterministic_empty(self, build_task_dir):
        """任务目录有效但 run_id 不存在 → ok=False / found=False / run=None，不抛异常。"""
        task_dir = build_task_dir()
        res = detail(str(task_dir), "no-such-run")

        assert res["ok"] is False
        assert res["found"] is False
        assert res["run"] is None
        assert res["reason"] == "run_not_found"
        assert res["task_dir"] == str(task_dir)
        assert res["run_id"] == "no-such-run"
        assert res["errors"] == []

    def test_run_not_found_deterministic_same_output(self, build_task_dir):
        """同一输入两次调用空结果精确相等（确定性）。"""
        task_dir = build_task_dir()
        assert detail(str(task_dir), "no-such-run") == detail(str(task_dir), "no-such-run")

    def test_run_id_invalid_no_raise(self, build_task_dir):
        """畸形 run_id（None/空串/非字符串）→ 确定性空结果，不抛异常。"""
        task_dir = build_task_dir()
        for bad in (None, "", "   ", 123, 3.14):
            res = detail(str(task_dir), bad)
            assert res["ok"] is False, repr(bad)
            assert res["found"] is False, repr(bad)
            assert res["run"] is None, repr(bad)


class TestTaskDirUnavailable:
    def test_task_dir_missing_no_raise(self, tmp_path):
        """任务目录不存在 → 空结果 reason=task_dir_unavailable，不抛异常。"""
        res = detail(str(tmp_path / "no-such-dir"), "run-20260823-001")
        assert res["ok"] is False
        assert res["found"] is False
        assert res["run"] is None
        assert res["reason"] == "task_dir_unavailable"

    def test_task_yaml_missing_no_raise(self, build_task_dir):
        """任务目录存在但 task.yaml 缺失（m01 整体空降级）→ 空结果，不抛异常。"""
        task_dir = build_task_dir(with_task_yaml=False)
        res = detail(str(task_dir), "run-20260823-001")
        assert res["ok"] is False
        assert res["reason"] == "task_dir_unavailable"

    def test_task_dir_none_no_raise(self):
        """task_dir 为 None → 空结果，不抛异常。"""
        res = detail(None, "run-20260823-001")
        assert res["ok"] is False
        assert res["reason"] == "task_dir_unavailable"


class TestEmptyResultContract:
    def test_empty_result_structure_fixed(self):
        """空结果结构固定（确定性字段顺序），且与自身相等。"""
        e1 = empty_result("/t", "r1")
        assert list(e1.keys()) == [
            "ok", "task_dir", "run_id", "found", "run", "reason", "errors",
        ]
        assert e1 == empty_result("/t", "r1")
        assert empty_result() == empty_result()

    def test_empty_result_never_raises(self):
        """空结果工厂在任意参数下不抛异常。"""
        for args in ((), (None,), ("/t", "r"), ("/t", "r", "x"), ("/t", "r", "x", ["e"])):
            res = empty_result(*args)
            assert isinstance(res, dict)
            assert res["ok"] is False
