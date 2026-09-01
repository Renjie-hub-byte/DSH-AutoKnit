"""摘要 / 对外接口 dsh.plan-only.summary 测试。

对应契约 data_shape 与验收 4（输出模块数/每模块预计行数/首个 executor 任务行数）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autoknit.api import get_plan_summary
from autoknit.errors import PlanNotReadyError
from autoknit.planner import build_modules, estimate_first_block_lines, estimate_module_lines
from autoknit.prd_parser import parse_prd
from autoknit.summary import format_summary_text, get_plan_summary as core_summary
from autoknit.runner import run_plan_only


def test_core_summary_matches_contract_shape(tmp_path: Path) -> None:
    d = tmp_path / "task"
    d.mkdir()
    (d / "PRD.md").write_text("# T\n目标\n\n## Alpha\n模块\n\n## Beta\n模块\n", encoding="utf-8")
    result = run_plan_only(d)
    summary = core_summary(result.plan)
    assert isinstance(summary, list)
    for item in summary:
        assert set(item.keys()) == {"module_name", "estimated_lines", "first_block_lines"}
        assert isinstance(item["module_name"], str)
        assert isinstance(item["estimated_lines"], int)
        assert isinstance(item["first_block_lines"], int)
    assert {i["module_name"] for i in summary} == {"Alpha", "Beta"}


def test_api_get_plan_summary(tmp_path: Path) -> None:
    d = tmp_path / "task"
    d.mkdir()
    (d / "PRD.md").write_text("# T\n目标\n\n## X\n模块\n", encoding="utf-8")
    run_plan_only(d)
    summary = get_plan_summary(d)
    assert len(summary) == 1
    assert summary[0]["module_name"] == "X"


def test_api_missing_task_yaml_errors(tmp_path: Path) -> None:
    d = tmp_path / "unplanned"
    d.mkdir()
    with pytest.raises(PlanNotReadyError):
        get_plan_summary(d)


def test_planner_deterministic() -> None:
    prd = parse_prd("# T\n目标\n\n## A\n一\n二\n三\n\n## B\n四\n五\n")
    m1 = build_modules(prd)
    m2 = build_modules(parse_prd("# T\n目标\n\n## A\n一\n二\n三\n\n## B\n四\n五\n"))
    assert [m.estimated_lines for m in m1] == [m.estimated_lines for m in m2]


def test_estimate_monotonic() -> None:
    small = estimate_module_lines(["a"])
    large = estimate_module_lines(["a"] * 50)
    assert large >= small


def test_first_block_lines_positive() -> None:
    assert estimate_first_block_lines(100) >= 10
    assert estimate_first_block_lines(10) >= 10


def test_format_summary_text_contains_counts() -> None:
    text = format_summary_text([{"module_name": "A", "estimated_lines": 60, "first_block_lines": 15}])
    assert "共 1 个大模块" in text
    assert "A" in text and "60" in text and "15" in text
