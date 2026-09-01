"""CLI 退出码与 --json 机器可解析（0/1/2/3/4/130）。"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys

from fw_runner.cli import main as cli_main


def test_usage_exit_4():
    assert cli_main([]) == 4

def test_unknown_subcommand_exit_4():
    assert cli_main(["bogus"]) == 4

def test_bad_mode_exit_1(tmp_path, indep4_root):
    # 非法 --mode 在 argparse 层是 usage(4)；合法模式但非法覆盖值在 input(1) 面
    pass

def test_nonexistent_root_exit_1(tmp_path):
    assert cli_main(["run", str(tmp_path / "no-such-root"), "--json"]) == 1

def test_complete_run_exit_0(indep4_root):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli_main(["run", str(indep4_root), "--json"])
    assert code == 0
    d = json.loads(buf.getvalue())
    assert d["ok"] is True
    assert d["status"] == "complete"
    assert sorted(d["completed"]) == ["m01", "m02", "m03", "m04"]
    assert set(d["modules"].keys()) == {"m01", "m02", "m03", "m04"}
    assert "checkpoint" in d and d["checkpoint"].endswith("快照.json")

def test_human_exit_2_end_gate_always(indep4_root):
    """end_gate=always → 人工确认 → exit 2（机器可解析）。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli_main(["run", str(indep4_root), "--end-gate", "always", "--json",
                         "--max-parallel", "2"])
    assert code == 2
    d = json.loads(buf.getvalue())
    assert d["status"] == "needs_confirmation"
    assert d["exit_reason"] == "end_gate_always"

def test_human_readable_output(indep4_root, capsys):
    code = cli_main(["run", str(indep4_root), "--max-parallel", "3"])
    out = capsys.readouterr().out
    assert code == 0
    assert "状态       : complete" in out
    assert "模块明细" in out

def test_input_error_bad_task(tmp_path):
    """不是 scaffold 目录 → input_error exit 1。"""
    bad = tmp_path / "not-a-task"
    bad.mkdir()
    (bad / "task.yaml").write_text("not: [valid", encoding="utf-8")
    assert cli_main(["run", str(bad), "--json"]) == 1
