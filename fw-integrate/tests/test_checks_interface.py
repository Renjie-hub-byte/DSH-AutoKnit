"""check_interfaces 单元：契约区 vs 模块 read_api 四类发现。"""
from __future__ import annotations

from helpers import build_task, fill_contract, make_complete_snapshot, module, module_dir, set_review_status_done
from fw_integrate.context import load_integrate_context
from fw_integrate.checks import check_interfaces


def _two_mod_root(tmp_path):
    mods = [module("m01", "甲", deps=[],
                   interfaces=[{"path": "/api/order/*", "method": ["POST", "PUT"], "note": "写入"}]),
            module("m02", "乙", deps=["m01"],
                   interfaces=[{"path": "/api/order/*", "method": ["GET"], "note": "查询"}])]
    root = build_task(tmp_path, "接口单元", mods)
    make_complete_snapshot(root)
    set_review_status_done(root)
    return root


def test_match_no_errors(tmp_path):
    root = _two_mod_root(tmp_path)
    ic = load_integrate_context(root, require_complete=False)
    res = check_interfaces(ic)
    assert res.ok
    assert not res.errors and not res.warnings


def test_contract_missing_in_module_read_api(tmp_path):
    """契约区登记给 m01 的 PUT 从 m01/contract.yaml read_api 删除 → error 指出 m01。"""
    root = _two_mod_root(tmp_path)
    p = module_dir(root, "m01") / "contract.yaml"
    text = p.read_text(encoding="utf-8").replace('method: ["POST", "PUT"]', 'method: ["POST"]')
    p.write_text(text, encoding="utf-8")
    ic = load_integrate_context(root, require_complete=False)
    res = check_interfaces(ic)
    assert not res.ok
    kinds = {f.kind for f in res.errors}
    assert "contract_vs_module_missing" in kinds
    assert any(f.module == "m01" and f.method == "PUT" for f in res.errors)


def test_cross_module_duplicate_names_both(tmp_path):
    """m02 read_api 抢注 m01 的 POST → error 同时带 m01 与 m02。"""
    root = _two_mod_root(tmp_path)
    fill_contract(module_dir(root, "m02"), ["m01"], ["src/data/x.json"], "数据",
                  read_api_add={"path": "/api/order/*", "method": ["POST"]})
    ic = load_integrate_context(root, require_complete=False)
    res = check_interfaces(ic)
    assert not res.ok
    dup = [f for f in res.errors if f.kind == "cross_module_duplicate"]
    assert dup
    assert any({f.module, f.module_b} == {"m01", "m02"} for f in dup)


def test_unregistered_interface_is_warning(tmp_path):
    """m02 read_api 声明契约区未登记的全新接口 → warning（非 error）。"""
    root = _two_mod_root(tmp_path)
    fill_contract(module_dir(root, "m02"), ["m01"], ["src/data/x.json"], "数据",
                  read_api_add={"path": "/api/extra/*", "method": ["DELETE"]})
    ic = load_integrate_context(root, require_complete=False)
    res = check_interfaces(ic)
    assert res.ok                       # 无 error
    assert any(f.kind == "unregistered" and f.severity == "warning" for f in res.warnings)
