"""run --resume-from-checkpoint 接续测试。

对应验收 4（用同一 task.yaml 继续 autoknit run 能接上、不重复规划；
run --resume-from-checkpoint 识别 checkpoint）与摘要格式微调后的回归。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from autoknit.errors import PlanNotReadyError
from autoknit.runner import run_plan_only
from autoknit.resume import resume_from_checkpoint

PRD = "# T\n目标\n\n## Alpha\n模块A\n\n## Beta\n模块B\n"


@pytest.fixture()
def planned_dir(tmp_path: Path) -> Path:
    d = tmp_path / "task"
    d.mkdir()
    (d / "PRD.md").write_text(PRD, encoding="utf-8")
    run_plan_only(d)
    return d


def test_resume_from_checkpoint_returns_plan(tmp_path: Path) -> None:
    d = tmp_path / "task"
    d.mkdir()
    (d / "PRD.md").write_text(PRD, encoding="utf-8")
    run_plan_only(d)
    result = resume_from_checkpoint(d)
    assert [m.name for m in result.plan.modules] == ["Alpha", "Beta"]
    assert result.task_yaml_path.exists()


def test_resume_uses_same_task_yaml_not_replanning(planned_dir: Path) -> None:
    task_yaml_before = (planned_dir / "task.yaml").read_text(encoding="utf-8")
    events_before = (planned_dir / "总日志" / "events.jsonl").read_text(encoding="utf-8")
    resume_from_checkpoint(planned_dir)
    # task.yaml 不变（未重复规划）、events 是 append（新增一条 run_resumed，不重跑规划）。
    assert (planned_dir / "task.yaml").read_text(encoding="utf-8") == task_yaml_before
    events_after = (planned_dir / "总日志" / "events.jsonl").read_text(encoding="utf-8")
    assert events_after.startswith(events_before)  # append-only
    kinds = [json.loads(line)["kind"] for line in events_after.splitlines() if line.strip()]
    assert "run_resumed" in kinds


def test_resume_no_forbidden_roles_stages(planned_dir: Path) -> None:
    resume_from_checkpoint(planned_dir)
    events = [
        json.loads(line)
        for line in (planned_dir / "总日志" / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert {e["role"] for e in events} == {"planner"}
    assert not ({e["stage"] for e in events} & {"exec", "audit", "split"})
    tokens = json.loads((planned_dir / "总日志" / "tokens.json").read_text(encoding="utf-8"))
    assert tokens["roles_seen"] == ["planner"]
    assert tokens["llm_requests"] == 0
    assert tokens["forbidden_events"] == 0


def test_resume_without_plan_raises(tmp_path: Path) -> None:
    d = tmp_path / "noplan"
    d.mkdir()
    (d / "PRD.md").write_text(PRD, encoding="utf-8")
    with pytest.raises(PlanNotReadyError):
        resume_from_checkpoint(d)


def test_cli_run_resume_exits_zero(planned_dir: Path, cli_env: dict[str, str]) -> None:
    proc = run_cli(["run", str(planned_dir), "--resume-from-checkpoint"], cli_env)
    assert proc.returncode == 0, proc.stderr
    assert "run 已接续" in proc.stdout
    assert "未重复规划" in proc.stdout


def test_cli_run_requires_resume_flag(planned_dir: Path, cli_env: dict[str, str]) -> None:
    proc = run_cli(["run", str(planned_dir)], cli_env)
    assert proc.returncode != 0
    assert "--resume-from-checkpoint" in proc.stderr


def test_cli_run_missing_plan_errors(tmp_path: Path, cli_env: dict[str, str]) -> None:
    d = tmp_path / "noplan"
    d.mkdir()
    (d / "PRD.md").write_text(PRD, encoding="utf-8")
    proc = run_cli(["run", str(d), "--resume-from-checkpoint"], cli_env)
    assert proc.returncode == 3  # PlanNotReadyError 确定性空降级
    assert "plan checkpoint" in proc.stderr


def test_run_help_mentions_resume(cli_env: dict[str, str]) -> None:
    proc = run_cli(["run", "--help"], cli_env)
    assert proc.returncode == 0
    assert "--resume-from-checkpoint" in proc.stdout


def run_cli(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "autoknit", *args],
        capture_output=True,
        text=True,
        env=env,
    )
