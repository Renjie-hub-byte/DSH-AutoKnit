"""fw-integrate CLI：退出码 0/1/2/4 机器可解析；--json 字段；run 全流程。"""
from __future__ import annotations

import json
import subprocess
import sys

from helpers import (conforming_executor, module, build_task, module_dir, run_runner_inline,
                     fill_contract, make_complete_snapshot, set_review_status_done)


def _env():
    """子进程环境：继承父进程（PATH 等），叠加 PYTHONPATH（runner demo 脚本子进程需要 PATH）。"""
    import os
    env = dict(os.environ)
    env["PYTHONPATH"] = ":".join(sys.path)
    return env


def _run_cli(*args, cwd=None):
    return subprocess.run([sys.executable, "-m", "fw_integrate.cli", *args],
                          capture_output=True, text=True, env=_env(), cwd=cwd)


def test_cli_usage_exit4():
    p = _run_cli()                 # 无子命令
    assert p.returncode == 4
    p2 = _run_cli("bogus", "/tmp/x")
    assert p2.returncode == 4


def test_cli_check_pass_exit0(tmp_path):
    mods = [module("m01", "甲", deps=[]), module("m02", "乙", deps=["m01"])]
    baseline = {"will_have": [
        "订单数据落盘为 JSON（src/data/orders.json 结构按契约）",
        "清洗模块产出标准化订单记录（含字段校验）",
    ], "will_not_have": ["不做实时流式处理", "不做支付与风控联动"]}
    root = build_task(tmp_path, "CLI-check通过", mods, baseline=baseline)
    run_runner_inline(root, conforming_executor(root))
    p = _run_cli("check", str(root), "--json")
    assert p.returncode == 0
    out = json.loads(p.stdout)
    assert out["ok"] is True
    assert out["baseline"]["counts"]["will_have_matched"] == 2


def test_cli_check_fail_exit2(tmp_path):
    from helpers import module as _m
    mods = [_m("m01", "甲", deps=[]), _m("m02", "乙", deps=["m01"])]
    root = build_task(tmp_path, "CLI-check失败", mods,
                      baseline={"will_have": ["绝无产物 src/probe/never.json"], "will_not_have": []})
    run_runner_inline(root, conforming_executor(root))
    p = _run_cli("check", str(root), "--json")
    assert p.returncode == 2
    out = json.loads(p.stdout)
    assert out["ok"] is False
    assert any("缺失" in e for e in out["errors"])


def test_cli_complete_requires_complete_snapshot(tmp_path):
    """未跑完（无快照）→ complete exit 1（input_error，防误归档）。"""
    root = build_task(tmp_path, "CLI-未跑完", [module("m01", "甲", deps=[])])
    p = _run_cli("complete", str(root), "--json")
    assert p.returncode == 1
    out = json.loads(p.stdout)
    assert out["status"] == "input_error"


def test_cli_input_error_exit1():
    p = _run_cli("check", "/tmp/definitely-not-exist-xyz", "--json")
    assert p.returncode == 1
    out = json.loads(p.stdout)
    assert "task.yaml" in out["message"]


def test_cli_run_demo_integration_failed(tmp_path):
    """run 全流程：demo executor 不交付契约产物 → 基线缺失 → integration_failed exit 2。"""
    from helpers import write_task_doc, module as _m
    mods = [_m("m01", "数据采集", deps=[],
               interfaces=[{"path": "/api/order/*", "method": ["POST", "PUT"], "note": "写入"}]),
            _m("m02", "数据清洗", deps=["m01"],
               interfaces=[{"path": "/api/order/*", "method": ["GET"], "note": "查询"}]),
            _m("m03", "报表输出", deps=["m02"],
               interfaces=[{"path": "/api/report/*", "method": ["POST"], "note": "报表"}])]
    root = build_task(tmp_path, "CLI-run集成失败", mods)
    p = _run_cli("run", str(root), "--json")
    assert p.returncode == 2
    out = json.loads(p.stdout)
    assert out["status"] == "integration_failed"
    assert (out.get("integration") or {}).get("status") == "failed"
