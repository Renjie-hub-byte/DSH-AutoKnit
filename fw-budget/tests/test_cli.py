"""fw-budget CLI 测试：四个子命令 + 机器可解析退出码。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from fw_budget.cli import main as cli_main
from helpers import build_task, module

FW1 = Path(__file__).resolve().parent.parent.parent
BIN = FW1 / "fw-budget" / "bin" / "fw-budget"


def _run_cli(argv):
    return cli_main(argv)


def test_status_json_parse(tmp_path, capsys):
    root = build_task(tmp_path, "CLI-status", [module("m01", "甲", deps=[])],
                      budget={"max_tokens": 500})
    code = _run_cli(["status", str(root), "--json"])
    captured = capsys.readouterr()
    assert code == 0
    doc = json.loads(captured.out)
    assert doc["task_root"] == str(root)
    assert doc["phase"] in ("ok", "warned", "stopped")
    assert "gate" in doc and "meter" in doc and "completed" in doc
    assert doc["gate"]["max_tokens"] == 500


def test_add_budget_cli(tmp_path, capsys):
    root = build_task(tmp_path, "CLI-add", [module("m01", "甲", deps=[])],
                      budget={"max_tokens": 300})
    code = _run_cli(["add-budget", str(root), "--max-tokens", "900", "--reason", "r", "--json"])
    assert code == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["old_max_tokens"] == 300 and doc["new_max_tokens"] == 900
    # 落地验证
    from fw_protocol import validate_file
    assert validate_file(root / "task.yaml").effective["budget"]["max_tokens"] == 900


def test_archive_cli(tmp_path, capsys):
    root = build_task(tmp_path, "CLI-archive", [module("m01", "甲", deps=[])])
    code = _run_cli(["archive", str(root), "--reason", "放弃", "--json"])
    assert code == 0
    doc = json.loads(capsys.readouterr().out)
    assert not Path(doc["old_path"]).exists()
    assert Path(doc["new_path"]).exists()
    assert (Path(doc["new_path"]) / "ARCHIVE.md").is_file()


def test_usage_error_exit_4():
    code = _run_cli([])                     # 缺子命令 → usage
    assert code == 4


def test_input_error_exit_1(tmp_path, capsys):
    code = _run_cli(["status", str(tmp_path / "nope"), "--json"])   # 任务根不存在
    assert code == 1


def test_bin_script_executable(tmp_path):
    """bin/fw-budget 可直接执行（--version exit 0）。"""
    res = subprocess.run([str(BIN), "--version"], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0 and "fw-budget" in res.stdout
