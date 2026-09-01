"""meter.py 单元测试：本地账本统计口径 + dsh 适配点行为。"""
from __future__ import annotations

import json

import pytest

from fw_budget.meter import DshTokenMeter, EventLogTokenMeter, summarize


def _emit(root, event, module, tokens, seq):
    line = json.dumps({
        "seq": seq, "run_id": "run-x", "event": event, "module": module,
        "detail": {"tokens": tokens},
    }, ensure_ascii=False)
    with open(root / "总日志" / "dispatch.jsonl", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def test_event_log_meter_aggregation(tmp_path):
    (tmp_path / "总日志").mkdir(parents=True)
    _emit(tmp_path, "executor.round.done", "m01", 300, 1)
    _emit(tmp_path, "auditor.round", "m01", 50, 2)
    _emit(tmp_path, "executor.round.done", "m02", 200, 3)
    _emit(tmp_path, "module.done", "m01", 0, 4)     # 非 token 事件应忽略
    _emit(tmp_path, "executor.round.done", "m02", 200, 5)   # m02 第二轮

    meter = EventLogTokenMeter(tmp_path)
    assert meter.total() == 750
    assert meter.per_module() == {"m01": 350, "m02": 400}
    assert meter.ranking()[0] == {"module": "m02", "tokens": 400}
    assert meter.events_seen() == 4
    assert meter.run_ids() == ["run-x"]


def test_event_log_meter_missing_file(tmp_path):
    meter = EventLogTokenMeter(tmp_path)
    assert meter.total() == 0
    assert meter.per_module() == {}
    assert meter.ranking() == []


def test_dsh_meter_fallback(tmp_path, monkeypatch):
    """未接入 dsh → source=fallback（本地账本）。"""
    (tmp_path / "总日志").mkdir(parents=True)
    _emit(tmp_path, "executor.round.done", "m01", 111, 1)
    m = DshTokenMeter(tmp_path)
    assert m.source == "fallback"
    assert m.total() == 111
    rep = summarize(tmp_path, meter=m)
    assert rep.source == "fallback"
    assert rep.to_dict()["total"] == 111


def test_dsh_meter_adapter_point(tmp_path, monkeypatch):
    """适配点：_query_dsh 返回数据 → source=dsh（真实接入形态）。"""
    (tmp_path / "总日志").mkdir(parents=True)

    class FakeDsh(DshTokenMeter):
        def _query_dsh(self):
            # 模拟 dsh token-meter 跨会话汇总返回（真实接入时替换此实现，见 docs/budget-spec.md）
            return {"total": 999, "per_module": {"m01": 600, "m02": 399}}

    m = FakeDsh(tmp_path)
    assert m.source == "dsh"
    assert m.total() == 999
    assert m.per_module() == {"m01": 600, "m02": 399}
    assert m.ranking()[0] == {"module": "m01", "tokens": 600}
    assert m.source_name() == "dsh"
