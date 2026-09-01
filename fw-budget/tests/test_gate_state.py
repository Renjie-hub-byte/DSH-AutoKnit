"""gate_state.py 单元测试：budget effective 默认值 / 闸门重建 / 累计消耗灌回 / check_now。"""
from __future__ import annotations

from helpers import build_task, module

from fw_budget.gate_state import (
    BudgetInputError, build_budget_gate, check_now, load_effective_budget,
)
from fw_budget.meter import EventLogTokenMeter


def test_load_effective_budget_defaults(tmp_path):
    """缺省字段由 fw-protocol effective 补全（warn_at=0.7 / stop_at=1.0）。"""
    root = build_task(tmp_path, "预算默认值", [module("m01", "甲", deps=[])],
                      budget={"max_tokens": 100000})   # 不给 warn/stop/per_module
    b = load_effective_budget(root)
    assert b["max_tokens"] == 100000
    assert b["warn_at"] == 0.7
    assert b["stop_at"] == 1.0


def test_build_gate_records_history(tmp_path):
    """resume 前重建闸门：历史累计消耗被 record 进去（跨 resume 不失忆）。"""
    root = build_task(tmp_path, "重建闸门", [module("m01", "甲", deps=[]),
                                             module("m02", "乙", deps=[])],
                      budget={"max_tokens": 1000, "warn_at": 0.7, "stop_at": 1.0})
    # 手工写两条带 token 的事件（模拟已发生的消耗 600/1000=60%）
    import json
    (root / "总日志").mkdir(parents=True, exist_ok=True)
    evs = [
        {"seq": 1, "run_id": "r1", "event": "executor.round.done", "module": "m01",
         "detail": {"tokens": 400}},
        {"seq": 2, "run_id": "r1", "event": "auditor.round", "module": "m01",
         "detail": {"tokens": 200}},
    ]
    with open(root / "总日志" / "dispatch.jsonl", "a", encoding="utf-8") as f:
        for ev in evs:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")

    gate = build_budget_gate(root, meter=EventLogTokenMeter(root))
    assert gate.used == 600
    assert gate.per_module == {"m01": 600}
    st = gate.check()
    assert st.stop is False and st.warned is False     # 600/1000=60% < warn_at 0.7


def test_check_now_machine_parseable(tmp_path):
    root = build_task(tmp_path, "check-now", [module("m01", "甲", deps=[])],
                      budget={"max_tokens": 500, "warn_at": 0.7, "stop_at": 1.0})
    out = check_now(root)
    assert "budget" in out and "ranking" in out
    assert out["budget"]["max_tokens"] == 500


def test_load_effective_rejects_invalid_task(tmp_path):
    """非法任务书 → 预算拒绝读取（BudgetInputError）。"""
    root = tmp_path / "bad"
    root.mkdir()
    (root / "task.yaml").write_text("modules: []\n", encoding="utf-8")   # 结构非法
    try:
        load_effective_budget(root)
        raise AssertionError("非法任务书应抛 BudgetInputError")
    except BudgetInputError:
        pass
