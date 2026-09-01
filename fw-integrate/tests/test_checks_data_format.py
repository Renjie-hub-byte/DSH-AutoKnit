"""check_data_format 单元：声明产物存在性 + 解析级格式校验。"""
from __future__ import annotations

from helpers import build_task, fill_contract, make_complete_snapshot, module, module_dir, set_review_status_done
from fw_integrate.context import load_integrate_context
from fw_integrate.checks import check_data_format


def _root(tmp_path):
    mods = [module("m01", "甲", deps=[]), module("m02", "乙", deps=["m01"])]
    root = build_task(tmp_path, "数据格式单元", mods)
    make_complete_snapshot(root)
    set_review_status_done(root)
    return root


def test_artifact_missing_is_error(tmp_path):
    root = _root(tmp_path)
    fill_contract(module_dir(root, "m01"), [], ["src/data/orders.json"], "订单")
    ic = load_integrate_context(root, require_complete=False)
    res = check_data_format(ic)
    assert not res.ok
    assert any(f.kind == "artifact_missing" and f.module == "m01" for f in res.errors)


def test_bad_json_is_error(tmp_path):
    root = _root(tmp_path)
    m1 = module_dir(root, "m01")
    (m1 / "src/data").mkdir(parents=True, exist_ok=True)
    (m1 / "src/data/orders.json").write_text("{not json!!", encoding="utf-8")
    fill_contract(m1, [], ["src/data/orders.json"], "订单")
    ic = load_integrate_context(root, require_complete=False)
    res = check_data_format(ic)
    assert not res.ok
    assert any(f.kind == "artifact_format_error" and f.module == "m01"
               and "JSON" in f.message for f in res.errors)


def test_good_json_and_csv_pass(tmp_path):
    root = _root(tmp_path)
    m1 = module_dir(root, "m01")
    (m1 / "src/data").mkdir(parents=True, exist_ok=True)
    (m1 / "src/data/orders.json").write_text('[{"a": 1}]', encoding="utf-8")
    fill_contract(m1, [], ["src/data/orders.json"], "订单")
    m2 = module_dir(root, "m02")
    (m2 / "src/data").mkdir(parents=True, exist_ok=True)
    (m2 / "src/data/daily.csv").write_text("date,total\n2026-08-21,1\n", encoding="utf-8")
    fill_contract(m2, ["m01"], ["src/data/daily.csv"], "统计")
    ic = load_integrate_context(root, require_complete=False)
    res = check_data_format(ic)
    assert res.ok
    assert all(f.kind == "artifact_ok" for f in res.infos)


def test_no_artifacts_is_info_not_error(tmp_path):
    root = _root(tmp_path)
    ic = load_integrate_context(root, require_complete=False)
    res = check_data_format(ic)
    assert res.ok
    assert all(f.kind == "no_artifacts" and f.severity == "info" for f in res.infos)
