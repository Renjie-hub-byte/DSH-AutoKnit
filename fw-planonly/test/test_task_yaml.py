"""task.yaml 构建 / 落盘 / 读取校验测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from autoknit.errors import InvalidTaskDirError
from autoknit.planner import build_modules
from autoknit.prd_parser import parse_prd
from autoknit.task_yaml import load_plan_from_task_yaml, save_task_yaml
from autoknit.runner import run_plan_only


def _sample_plan():
    prd = parse_prd("# Demo\n目标\n\n## A\n模块A内容\n\n## B\n模块B内容\n")
    return prd, build_modules(prd)


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    from autoknit.models import TaskPlan

    prd, modules = _sample_plan()
    plan = TaskPlan(task_name=prd.title, goal=prd.goal, execution_order=["A", "B"], modules=modules)
    saved = save_task_yaml(plan, tmp_path / "task.yaml")
    assert saved.is_file()

    loaded = load_plan_from_task_yaml(saved)
    assert loaded.task_name == prd.title
    assert [m.name for m in loaded.modules] == ["A", "B"]
    assert loaded.modules[0].first_block.estimate_lines > 0


def test_task_yaml_contains_contract_and_interface(tmp_path: Path) -> None:
    from autoknit.models import TaskPlan

    prd, modules = _sample_plan()
    plan = TaskPlan(task_name=prd.title, goal=prd.goal, execution_order=[], modules=modules)
    saved = save_task_yaml(plan, tmp_path / "task.yaml")
    data = yaml.safe_load(saved.read_text(encoding="utf-8"))
    assert "data_contract" in data
    assert data["data_contract"]["shared_enums"]["stage"] == ["planning", "exec", "audit", "split", "idle"]
    paths = [i["path"] for i in data["interfaces"]]
    assert "dsh.plan-only.summary" in paths


def test_load_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(InvalidTaskDirError):
        load_plan_from_task_yaml(tmp_path / "nope.yaml")


def test_run_plan_only_produces_valid_task_yaml(tmp_path: Path) -> None:
    d = tmp_path / "task"
    d.mkdir()
    (d / "PRD.md").write_text("# T\n目标\n\n## X\n模块\n", encoding="utf-8")
    result = run_plan_only(d)
    assert result.task_yaml_path.exists()
    reloaded = load_plan_from_task_yaml(result.task_yaml_path)
    assert reloaded.modules  # 非空
