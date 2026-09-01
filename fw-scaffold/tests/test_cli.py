"""CLI 退出码与机器可解析输出（0/1/2/3/4）。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FW_PROTOCOL = ROOT.parent / "fw-protocol"


def run_cli(*args, env_extra=None):
    env = dict(os.environ)
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    proc = subprocess.run(
        [sys.executable, "-m", "fw_scaffold.cli", *args],
        capture_output=True, text=True, cwd=str(ROOT), env=env)
    return proc


def test_cli_valid_creates_tree(valid_task, tmp_path):
    proc = run_cli(str(valid_task), "--output", str(tmp_path / "out"), "--json")
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["ok"] is True
    assert data["status"] == "created"
    assert (tmp_path / "out" / "任务-测试订单管道_2026-08-21" / "task.yaml").exists()


def test_cli_invalid_task_exit_1(valid_task, tmp_path):
    import yaml
    doc = yaml.safe_load(valid_task.read_text(encoding="utf-8"))
    doc["modules"][1]["dependencies"] = ["m03"]   # 制造依赖环 m01→m02→m03→m02? 直接自环更稳
    doc["modules"][0]["dependencies"] = ["m02"]
    cyclic = tmp_path / "cycle.yaml"
    cyclic.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    proc = run_cli(str(cyclic), "--output", str(tmp_path / "out2"), "--json")
    assert proc.returncode == 1
    data = json.loads(proc.stdout)
    assert data["ok"] is False and data["status"] == "task_invalid"
    assert any(e["code"] == "dep_cycle" for e in data["errors"])
    assert not (tmp_path / "out2").exists()          # 非法任务书不生成


def test_cli_version_mismatch_exit_2(valid_task, tmp_path):
    out = tmp_path / "out"
    proc1 = run_cli(str(valid_task), "--output", str(out))
    assert proc1.returncode == 0
    target = out / "任务-测试订单管道_2026-08-21" / "skeleton.md"
    target.write_text(target.read_text(encoding="utf-8") + "\n# x\n", encoding="utf-8")
    proc2 = run_cli(str(valid_task), "--output", str(out), "--json")
    assert proc2.returncode == 2
    assert json.loads(proc2.stdout)["status"] == "version_mismatch"


def test_cli_missing_file_exit_3(tmp_path):
    proc = run_cli(str(tmp_path / "nope.yaml"), "--json")
    assert proc.returncode == 3          # fw-protocol 读取失败 → CLI 兜底 EXIT_IO
    assert json.loads(proc.stdout)["status"] == "io_error"


def test_cli_usage_exit_4():
    proc = run_cli()                     # 缺参数
    assert proc.returncode == 4          # argparse 用法错误已覆盖为 exit 4
