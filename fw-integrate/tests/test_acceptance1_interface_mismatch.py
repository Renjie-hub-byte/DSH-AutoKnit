"""需求6 验收1：接口不匹配（两个模块）→ 集成阶段报错并指出具体哪两个模块。

复现形态：m02/contract.yaml read_api 把 m01 的方法（POST /api/order/*）也声明了
（契约区登记给 m01；m02 抢注）→ 集成检查必须报错且错误消息同时带 m01 与 m02。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from helpers import build_task, make_complete_snapshot, module, module_dir, set_review_status_done
from fw_integrate.context import load_integrate_context
from fw_integrate.report import run_checks


def _mismatch_root(tmp_path) -> Path:
    mods = [module("m01", "数据采集", deps=[],
                   interfaces=[{"path": "/api/order/*", "method": ["POST", "PUT"],
                                "note": "订单写入（数据源侧）"}]),
            module("m02", "数据清洗", deps=["m01"],
                   interfaces=[{"path": "/api/order/*", "method": ["GET"],
                                "note": "清洗后订单查询"}]),
            module("m03", "报表输出", deps=["m02"],
                   interfaces=[{"path": "/api/report/*", "method": ["POST"],
                                "note": "报表生成"}])]
    root = build_task(tmp_path, "验收1-接口不匹配", mods, baseline=None)
    make_complete_snapshot(root)          # 快照 complete（允许 complete/归档类检查）
    set_review_status_done(root)
    # 篡改 m02 read_api：把 m01 的 POST 也声明进来
    from helpers import fill_contract
    fill_contract(module_dir(root, "m02"), ["m01"], ["src/data/cleaned_orders.json"],
                  "清洗后订单", read_api_add={"path": "/api/order/*", "method": ["POST"]})
    return root


def test_acceptance1_programmatic(tmp_path):
    root = _mismatch_root(tmp_path)
    ic = load_integrate_context(root, require_complete=False)
    report = run_checks(ic)
    assert report.ok is False
    # 错误消息必须同时指出两个模块
    msgs = "\n".join(report.errors)
    assert "m01" in msgs and "m02" in msgs
    # 具体错误种类（机器可解析）
    kinds = {f.kind for f in report.interface.errors}
    assert "cross_module_duplicate" in kinds
    dup = [f for f in report.interface.errors if f.kind == "cross_module_duplicate"]
    assert dup and all(f.module_b for f in dup)
    assert {f.module for f in dup} == {"m01", "m02"} or any(
        {f.module, f.module_b} == {"m01", "m02"} for f in dup)


def test_acceptance1_cli_names_two_modules(tmp_path):
    root = _mismatch_root(tmp_path)
    import os
    env = dict(os.environ)
    env["PYTHONPATH"] = ":".join(sys.path)
    proc = subprocess.run(
        [sys.executable, "-m", "fw_integrate.cli", "check", str(root), "--json"],
        capture_output=True, text=True, env=env, cwd=str(root.parent))
    assert proc.returncode == 2          # 集成失败 → 回人语义 exit 2
    out = json.loads(proc.stdout)
    assert out["ok"] is False
    msgs = "\n".join(out["errors"])
    assert "m01" in msgs and "m02" in msgs


def test_conforming_root_has_no_interface_error(conform_root):
    """对照：全交付一致根不应报接口错误（防误报）。"""
    ic = load_integrate_context(conform_root, require_complete=True)
    report = run_checks(ic)
    assert report.interface.ok
    assert not report.interface.errors
