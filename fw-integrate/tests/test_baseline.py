"""预测基线对照单元：匹配/缺失/违反（will_have / will_not_have）与关键词提取。"""
from __future__ import annotations

from helpers import build_task, fill_contract, make_complete_snapshot, module, module_dir, set_review_status_done
from fw_integrate.context import load_integrate_context
from fw_integrate.baseline import check_baseline, extract_keywords


def _root(tmp_path, baseline=None):
    mods = [module("m01", "甲", deps=[]), module("m02", "乙", deps=["m01"])]
    root = build_task(tmp_path, "基线单元", mods, baseline=baseline)
    make_complete_snapshot(root)
    set_review_status_done(root)
    return root


def test_keyword_extraction():
    kws = extract_keywords("订单数据落盘为 JSON（src/data/orders.json 结构按契约）")
    assert "src/data/orders.json" in kws
    assert any("订单数据落盘" in k or "订单" in k and "落盘" in k for k in kws) or "订单数据落盘为" in kws
    assert "json" in kws
    kws2 = extract_keywords("不做实时流式处理（本任务是批处理）")
    assert any("实时" in k for k in kws2) or "不做实时流式处理" in kws2


def test_will_have_matched_and_missing(tmp_path):
    baseline = {"will_have": ["订单数据落盘为 JSON（src/data/orders.json）"],
                "will_not_have": ["不做实时"]}
    root = _root(tmp_path, baseline=baseline)
    m1 = module_dir(root, "m01")
    (m1 / "src/data").mkdir(parents=True, exist_ok=True)
    (m1 / "src/data/orders.json").write_text("[]", encoding="utf-8")
    ic = load_integrate_context(root, require_complete=False)
    res = check_baseline(ic)
    assert res.matched == ["订单数据落盘为 JSON（src/data/orders.json）"]
    assert not res.missing
    assert res.clean == ["不做实时"]


def test_will_not_have_violation(tmp_path):
    baseline = {"will_have": ["订单数据落盘为 JSON（src/data/orders.json）"],
                "will_not_have": ["不做支付与风控联动"]}
    root = _root(tmp_path, baseline=baseline)
    m1 = module_dir(root, "m01")
    (m1 / "src/data").mkdir(parents=True, exist_ok=True)
    (m1 / "src/data/orders.json").write_text("[]", encoding="utf-8")
    # 交付说明出现"支付"关键词 → violation
    (m1 / "交付说明.md").write_text(
        (m1 / "交付说明.md").read_text(encoding="utf-8") + "\n- 已接入支付网关\n", encoding="utf-8")
    ic = load_integrate_context(root, require_complete=False)
    res = check_baseline(ic)
    assert res.ok is False
    assert res.violations == ["不做支付与风控联动"]
    assert res.clean == []


def test_template_echo_not_evidence(tmp_path):
    """skeleton.md / contract.yaml 模板回显基线文本 → 不作为证据（防误命中）。"""
    baseline = {"will_have": ["绝对不存在的产物 src/probe/never.yaml"],
                "will_not_have": ["不做实时流式处理（本任务是批处理）"]}
    root = _root(tmp_path, baseline=baseline)
    # 在 skeleton.md 里写同名基线文案（应被排除）
    (root / "skeleton.md").write_text("## 预测基线\n- src/probe/never.yaml\n- 不做实时流式处理\n",
                                      encoding="utf-8")
    ic = load_integrate_context(root, require_complete=False)
    res = check_baseline(ic)
    assert res.missing == ["绝对不存在的产物 src/probe/never.yaml"]
    assert res.clean == ["不做实时流式处理（本任务是批处理）"]   # 骨架回显不算违反


def test_deliverable_text_matches(tmp_path):
    """交付说明文本（executor 产出）命中基线关键词 → matched。"""
    baseline = {"will_have": ["清洗模块产出标准化订单记录（含字段校验）"], "will_not_have": []}
    root = _root(tmp_path, baseline=baseline)
    (module_dir(root, "m02") / "交付说明.md").write_text(
        (module_dir(root, "m02") / "交付说明.md").read_text(encoding="utf-8")
        + "\n- 清洗模块产出标准化订单记录（含字段校验）\n", encoding="utf-8")
    ic = load_integrate_context(root, require_complete=False)
    res = check_baseline(ic)
    assert res.matched == ["清洗模块产出标准化订单记录（含字段校验）"]
