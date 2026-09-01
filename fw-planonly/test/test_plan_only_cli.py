"""CLI 级验收测试：子命令注册、退出码、产物与输出。

对应验收 1/2/4（子命令可用 + 产出 task.yaml + stdout 摘要）。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SAMPLE_PRD = """# Demo Task
这是一个演示任务，用于验证 plan-only 流程。

## Module A
模块 A 的目标是处理核心逻辑。
- 负责输入解析
- 负责状态机

## Module B
模块 B 负责接入与测试。
- 写单测
- 跑回归
"""


@pytest.fixture()
def task_dir(tmp_path: Path) -> Path:
    d = tmp_path / "task"
    d.mkdir()
    (d / "PRD.md").write_text(SAMPLE_PRD, encoding="utf-8")
    return d


def run_cli(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "autoknit", *args],
        capture_output=True,
        text=True,
        env=env,
    )


def test_plan_only_subcommand_registered_and_exits_zero(task_dir: Path, cli_env: dict[str, str]) -> None:
    proc = run_cli(["plan-only", str(task_dir)], cli_env)
    assert proc.returncode == 0, proc.stderr
    assert "plan-only 规划摘要" in proc.stdout
    assert "退出码 0" in proc.stdout


def test_plan_only_produces_task_yaml(task_dir: Path, cli_env: dict[str, str]) -> None:
    proc = run_cli(["plan-only", str(task_dir)], cli_env)
    assert proc.returncode == 0, proc.stderr
    assert (task_dir / "task.yaml").is_file()


def test_plan_only_stdout_summary_has_module_info(task_dir: Path, cli_env: dict[str, str]) -> None:
    proc = run_cli(["plan-only", str(task_dir)], cli_env)
    assert proc.returncode == 0, proc.stderr
    assert "共 2 个大模块" in proc.stdout
    assert "Module A" in proc.stdout
    assert "Module B" in proc.stdout
    assert "预计" in proc.stdout
    assert "首个 executor 任务" in proc.stdout


def test_plan_only_writes_checkpoint(task_dir: Path, cli_env: dict[str, str]) -> None:
    proc = run_cli(["plan-only", str(task_dir)], cli_env)
    assert proc.returncode == 0, proc.stderr
    assert (task_dir / "总日志" / "快照.json").is_file()
    assert (task_dir / "总日志" / "plan_checkpoint.json").is_file()


def test_summary_subcommand_reads_existing_task_yaml(task_dir: Path, cli_env: dict[str, str]) -> None:
    assert run_cli(["plan-only", str(task_dir)], cli_env).returncode == 0
    proc = run_cli(["summary", str(task_dir)], cli_env)
    assert proc.returncode == 0, proc.stderr
    assert "Module A" in proc.stdout


def test_summary_missing_task_yaml_errors_deterministically(tmp_path: Path, cli_env: dict[str, str]) -> None:
    d = tmp_path / "notplanned"
    d.mkdir()
    (d / "PRD.md").write_text(SAMPLE_PRD, encoding="utf-8")
    proc = run_cli(["summary", str(d)], cli_env)
    assert proc.returncode != 0
    assert "task.yaml" in proc.stderr


def test_plan_only_missing_prd_errors(tmp_path: Path, cli_env: dict[str, str]) -> None:
    d = tmp_path / "noprd"
    d.mkdir()
    proc = run_cli(["plan-only", str(d)], cli_env)
    assert proc.returncode != 0
    assert "PRD" in proc.stderr


def test_help_mentions_plan_only(cli_env: dict[str, str]) -> None:
    proc = run_cli(["--help"], cli_env)
    assert proc.returncode == 0
    assert "plan-only" in proc.stdout
