"""需求6 验收2：预测基线对照 → 输出匹配/缺失清单。

复现形态：全交付根（m01 orders.json 落盘 / m03 日报 CSV 输出均真实交付）→ will_have
全部 matched（带证据路径）；另一棵根只交付部分（缺 CSV）→ missing 清单包含未交付项；
will_not_have 未命中 → clean（无违反）。
"""
from __future__ import annotations

from helpers import build_task, conforming_executor, make_complete_snapshot, module, \
    module_dir, run_runner_inline, set_review_status_done


def test_acceptance2_full_delivery_has_matched_list(conform_root):
    from fw_integrate.context import load_integrate_context
    from fw_integrate.report import run_checks
    ic = load_integrate_context(conform_root, require_complete=True)
    report = run_checks(ic)
    assert report.baseline.matched, "全交付根应全部 matched"
    for b in report.baseline.items:
        if b.kind == "will_have":
            assert b.status == "matched", f"will_have 应全 matched: {b.item}"
    # 证据必须有文件路径（不只关键词级）
    first = next(b for b in report.baseline.items if b.kind == "will_have")
    assert first.evidence, "matched 项必须带证据路径"
    assert not report.baseline.missing
    assert not report.baseline.violations


def test_acceptance2_partial_delivery_matched_and_missing(tmp_path):
    """只交付 m01（orders.json）→ 该项 matched；CSV 未交付 → missing（清单输出）。"""
    mods = [module("m01", "数据采集", deps=[],
                   interfaces=[{"path": "/api/order/*", "method": ["POST", "PUT"],
                                "note": "订单写入"}]),
            module("m02", "数据清洗", deps=["m01"],
                   interfaces=[{"path": "/api/order/*", "method": ["GET"], "note": "查询"}]),
            module("m03", "报表输出", deps=["m02"],
                   interfaces=[{"path": "/api/report/*", "method": ["POST"], "note": "报表"}])]
    from helpers import write_task_doc, fill_contract, append_delivery
    root = build_task(tmp_path, "验收2-部分交付", mods, baseline=None)
    make_complete_snapshot(root)
    set_review_status_done(root)
    # 只交付 m01 的 orders.json + 对应契约
    m1 = module_dir(root, "m01")
    (m1 / "src/data").mkdir(parents=True, exist_ok=True)
    (m1 / "src/data/orders.json").write_text('[{"order_id": "A1", "amount": 1}]', encoding="utf-8")
    fill_contract(m1, [], ["src/data/orders.json"], "订单 JSON")
    append_delivery(m1, "## 交付摘要\n- 订单数据已落盘为 JSON（src/data/orders.json）。\n")
    # m02/m03 交付说明不给基线文案
    from fw_integrate.context import load_integrate_context
    from fw_integrate.report import run_checks
    ic = load_integrate_context(root, require_complete=True)
    report = run_checks(ic)
    # 匹配/缺失清单（验收2 的核心断言）
    assert any("订单数据落盘为" in m for m in report.baseline.matched)
    assert any("订单统计 CSV" in m for m in report.baseline.missing)
    assert any("标准化订单记录" in m for m in report.baseline.missing)
    # 机器可解析结构
    d = report.baseline.to_dict()
    assert d["counts"]["will_have_matched"] == 1
    assert d["counts"]["will_have_missing"] == 2
    # 无 will_not_have 违反（交付说明未含 支付/实时流式 等）
    assert not report.baseline.violations
