"""check_data_dependency 单元：B 需要的输入是否 A 的 output 声明过。"""
from __future__ import annotations

from helpers import build_task, fill_contract, make_complete_snapshot, module, module_dir, set_review_status_done
from fw_integrate.context import load_integrate_context
from fw_integrate.checks import check_data_dependency


def _root(tmp_path):
    mods = [module("m01", "甲", deps=[]), module("m02", "乙", deps=["m01"])]
    root = build_task(tmp_path, "依赖单元", mods)
    make_complete_snapshot(root)
    set_review_status_done(root)
    return root


def test_input_from_module_without_output_is_error(tmp_path):
    """B(m02) 声明 input.from=[m01]，但 m01 未声明 output.artifacts → error（A 未声明过）。"""
    root = _root(tmp_path)
    fill_contract(module_dir(root, "m02"), ["m01"], [], "需要 m01 数据")
    ic = load_integrate_context(root, require_complete=False)
    res = check_data_dependency(ic)
    assert not res.ok
    assert any(f.kind == "input_not_declared" and f.module == "m02" and f.module_b == "m01"
               for f in res.errors)


def test_input_from_module_with_output_satisfied(tmp_path):
    root = _root(tmp_path)
    m1 = module_dir(root, "m01")
    (m1 / "src/data").mkdir(parents=True, exist_ok=True)
    (m1 / "src/data/orders.json").write_text("[]", encoding="utf-8")
    fill_contract(m1, [], ["src/data/orders.json"], "订单")
    fill_contract(module_dir(root, "m02"), ["m01"], ["src/data/cleaned.json"], "清洗")
    ic = load_integrate_context(root, require_complete=False)
    res = check_data_dependency(ic)
    assert res.ok
    assert any(f.kind == "dependency_satisfied" and f.module == "m02" and f.module_b == "m01"
               for f in res.infos)


def test_producer_artifact_missing_is_error(tmp_path):
    """A 声明了产物但文件缺失 → error（B 依赖的产物不真实存在）。"""
    root = _root(tmp_path)
    fill_contract(module_dir(root, "m01"), [], ["src/data/orders.json"], "订单")
    fill_contract(module_dir(root, "m02"), ["m01"], ["src/data/cleaned.json"], "清洗")
    ic = load_integrate_context(root, require_complete=False)
    res = check_data_dependency(ic)
    assert not res.ok
    assert any(f.kind == "producer_artifact_missing" for f in res.errors)


def test_shared_file_input(tmp_path):
    root = _root(tmp_path)
    (root / "shared" / "raw.csv").write_text("h\n1\n", encoding="utf-8")
    # 直接改 contract.yaml：input.from 含 shared/raw.csv
    p = module_dir(root, "m01") / "contract.yaml"
    text = p.read_text(encoding="utf-8").replace("  from: []", "  from: [shared/raw.csv]")
    p.write_text(text, encoding="utf-8")
    ic = load_integrate_context(root, require_complete=False)
    res = check_data_dependency(ic)
    assert any(f.kind == "shared_file_ok" and f.path == "shared/raw.csv" for f in res.infos)


def test_undeclared_consumption_is_warning(tmp_path):
    """m02 依赖 m01 但 input.from 未声明消费 → warning（提示性）。"""
    root = _root(tmp_path)
    ic = load_integrate_context(root, require_complete=False)
    res = check_data_dependency(ic)
    assert res.ok
    assert any(f.kind == "undeclared_consumption" and f.module == "m02" and f.module_b == "m01"
               for f in res.warnings)
