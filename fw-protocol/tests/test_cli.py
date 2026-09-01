"""CLI 验收：退出码 0/1/2 可机器解析；--json / --effective / --no-* 开关。"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parent.parent
EXAMPLES = PKG / "examples"

RUN = [sys.executable, "-m", "fw_protocol.cli"]


def run_cli(*args):
    return subprocess.run(RUN + list(args), cwd=PKG, capture_output=True, text=True)


def test_valid_exit_0(valid_task):  # noqa: F811
    p = run_cli(str(EXAMPLES / "task-valid.yaml"))
    assert p.returncode == 0


def test_cycle_exit_1(cycle_task):  # noqa: F811
    p = run_cli(str(EXAMPLES / "task-cycle.yaml"))
    assert p.returncode == 1
    assert "依赖环" in p.stdout


def test_interface_dup_exit_1(interface_dup_task):  # noqa: F811
    p = run_cli(str(EXAMPLES / "task-interface-dup.yaml"))
    assert p.returncode == 1
    assert "接口重复" in p.stdout


def test_conflict_exit_2(conflict_task):  # noqa: F811
    p = run_cli(str(EXAMPLES / "task-conflict.yaml"))
    assert p.returncode == 2
    assert "人工" in p.stdout


def test_json_machine_parseable(valid_task):  # noqa: F811
    p = run_cli(str(EXAMPLES / "task-valid.yaml"), "--json")
    assert p.returncode == 0
    d = json.loads(p.stdout)
    assert d["status"] == "pass"
    assert d["effective"]["runtime"]["max_parallel"] == 2


def test_json_conflict_status():
    p = run_cli(str(EXAMPLES / "task-conflict.yaml"), "--json")
    assert p.returncode == 2
    d = json.loads(p.stdout)
    assert d["status"] == "conflict"
    assert d["ok"] is True
    assert d["errors"] == []


def test_no_cycle_toggle():
    p = run_cli(str(EXAMPLES / "task-cycle.yaml"), "--no-cycle")
    assert p.returncode == 0


def test_effective_write(tmp_path):
    out = tmp_path / "eff.json"
    p = run_cli(str(EXAMPLES / "task-valid.yaml"), "--effective", str(out))
    assert p.returncode == 0
    assert out.exists()
    d = json.loads(out.read_text(encoding="utf-8"))
    # 任务书未写 executor_max_rounds → 默认 5（默认值补全生效）
    assert d["runtime"]["executor_max_rounds"] == 5


def test_missing_file_exit_3(tmp_path):
    p = run_cli(str(tmp_path / "nope.yaml"))
    assert p.returncode == 3
