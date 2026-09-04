# -*- coding: utf-8 -*-
"""AuditorOutcome 判定解析（m02 auditor 迁层②样板，四层容错契约推广）。

验收口径（任务书 m02 first_block acceptance）：
  5 类脏输入（markdown 围栏 / 尾逗号 / 判定词变体 / 字段错位 / 截断）
  → 正确归一化或留痕拒绝；meta.layer 回答第几层捞回；
  → 关键语义缺失（verdict/passed_count/total_count）绝不静默补 0 冒充判定（A4）。
"""
from __future__ import annotations

import json

import pytest

pydantic = pytest.importorskip("pydantic")  # noqa: F841

from fw_runner.llmjson import AuditorOutcome, parse_auditor_json, parse_auditor_payload  # noqa: E402


def _dump(**kw):
    base = {"verdict": "partial", "passed_count": 4, "total_count": 4}
    base.update(kw)
    return json.dumps(base, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 1) 格式病：markdown 围栏 —— 直解成功，layer=1，repair 未触发
# ---------------------------------------------------------------------------
class TestFenced:
    def test_json_fence_normalized(self):
        text = '```json\n{"verdict": "pass", "passed_count": 3, "total_count": 3}\n```'
        payload, errors, meta = parse_auditor_payload(text)
        assert errors == []
        assert payload["verdict"] == "pass"
        assert payload["passed_count"] == 3
        assert meta["layer"] == 1 and meta["repaired"] is False

    def test_fence_after_prose(self):
        text = ("审计完毕。\n```json\n"
                '{"verdict": "block", "passed_count": 0, "total_count": 5, '
                '"remaining_items": ["R1", "R3"], "evidence": ["缺 src"]}\n```')
        payload, errors, _ = parse_auditor_payload(text)
        assert errors == []
        assert payload["verdict"] == "block"
        assert payload["passed_count"] == 0
        assert payload["remaining_items"] == ["R1", "R3"]


# ---------------------------------------------------------------------------
# 2) 格式病：尾逗号 —— 需 json_repair 修复，layer=2，repaired=True
# ---------------------------------------------------------------------------
class TestTrailingComma:
    def test_trailing_comma_repaired(self):
        text = ('{"verdict": "partial", "passed_count": 3, "total_count": 4, '
                '"remaining_items": ["a", "b",], }')
        payload, errors, meta = parse_auditor_payload(text)
        assert errors == []
        assert payload["verdict"] == "partial"
        assert payload["passed_count"] == 3
        assert meta["layer"] == 2 and meta["repaired"] is True


# ---------------------------------------------------------------------------
# 3) 语义病：判定词变体 —— 归一化到三态 enum；认不出 → 拒绝不硬猜
# ---------------------------------------------------------------------------
class TestVerdictVariants:
    @pytest.mark.parametrize("variant,expect", [
        ("通过", "pass"), ("全部通过", "pass"), ("Pass", "pass"), (" PASS ", "pass"),
        ("部分通过", "partial"), ("部分满足", "partial"), ("partial", "partial"),
        ("不通过", "block"), ("blocked", "block"), ("验收失败", "block"),
    ])
    def test_variant_normalized(self, variant, expect):
        payload, errors, _ = parse_auditor_payload(_dump(verdict=variant))
        assert errors == []
        assert payload["verdict"] == expect

    def test_unrecognized_verdict_rejected(self):
        # 认不出就不乱猜：宁可留回人，不静默当 pass/partial 放行
        payload, errors, meta = parse_auditor_payload(_dump(verdict="maybe"))
        assert payload is None
        assert errors and meta["layer"] == 4
        assert any("verdict" in e for e in errors)


# ---------------------------------------------------------------------------
# 4) 结构病：字段错位 / 错型 —— 容错 coercion 或留痕拒绝
# ---------------------------------------------------------------------------
class TestFieldMismatch:
    def test_counts_as_numeric_string(self):
        payload, errors, _ = parse_auditor_payload(
            '{"verdict": "pass", "passed_count": "5", "total_count": "5.0"}')
        assert errors == []
        assert payload["passed_count"] == 5 and payload["total_count"] == 5

    def test_counts_swapped_rejected(self):
        # passed>total ⇒ 计数串位，拒绝而非猜（字段错位防线）
        payload, errors, _ = parse_auditor_payload(_dump(passed_count=6, total_count=5))
        assert payload is None
        assert any("串位" in e for e in errors)

    def test_remaining_items_bare_phrase_list(self):
        # 列表字段写成顿号/逗号裸串 → 拆成列表（R1：交接归一化后的对象）
        payload, errors, _ = parse_auditor_payload(
            '{"verdict": "partial", "passed_count": 2, "total_count": 4, '
            '"remaining_items": "R1、R3, R5"}')
        assert errors == []
        assert payload["remaining_items"] == ["R1", "R3", "R5"]

    def test_negative_count_rejected(self):
        payload, errors, _ = parse_auditor_payload(_dump(passed_count=-1, total_count=4))
        assert payload is None
        assert any("passed_count" in e for e in errors)


# ---------------------------------------------------------------------------
# A4：关键语义缺失 —— 留痕拒绝，绝不静默补 0
# ---------------------------------------------------------------------------
class TestNoSilentZero:
    @pytest.mark.parametrize("field", ["verdict", "passed_count", "total_count"])
    def test_missing_semantic_field_rejected(self, field):
        base = {"verdict": "partial", "passed_count": 3, "total_count": 4}
        del base[field]
        payload, errors, meta = parse_auditor_payload(json.dumps(base))
        assert payload is None            # 拒绝，不是补 0 冒充判定
        assert errors and meta["layer"] == 4
        assert any(field in e for e in errors)

    def test_null_semantic_field_rejected(self):
        payload, errors, _ = parse_auditor_payload(_dump(verdict=None, total_count=4))
        assert payload is None
        assert any("verdict" in e for e in errors)


# ---------------------------------------------------------------------------
# 5) 结构病：截断 —— 留痕（meta.truncated）+ 尝试 json_repair 补全
# ---------------------------------------------------------------------------
class TestTruncated:
    def test_unbalanced_tail_marked_truncated(self):
        # 括号不平衡的截断尾巴；能补全就给出对象，且 meta 一定带 truncated 标记
        text = ('前置说明 {"verdict": "partial", "passed_count": 2, '
                '"total_count": 4, "remaining_items": ["a", "b"')
        payload, errors, meta = parse_auditor_payload(text)
        assert meta["truncated"] is True
        # 修复/捞取结果二选一都算正确处理：要么归一化成功，要么拒绝并留痕
        if payload is not None:
            assert payload["verdict"] in ("pass", "partial", "block")
        else:
            assert errors

    def test_pure_prose_no_json_refused(self):
        payload, errors, meta = parse_auditor_payload("验收结论：判定 pass，通过 4/4")
        assert payload is None
        assert meta["layer"] == 4
        assert errors


# ---------------------------------------------------------------------------
# meta.layer / 一站式便捷入口 / 多候选 prefer
# ---------------------------------------------------------------------------
class TestMetaAndEntry:
    def test_layer_answer_recovery_stage(self):
        # 直解 = 1；repair = 2；失败兜底 = 4（层①是 prompt 的事，不在此）
        _, _, m1 = parse_auditor_payload(_dump())
        assert m1["layer"] == 1
        _, _, m2 = parse_auditor_payload('{"verdict":"pass","passed_count":1,"total_count":1,}')
        assert m2["layer"] == 2 and m2["repaired"] is True
        _, _, m4 = parse_auditor_payload("没有 JSON")
        assert m4["layer"] == 4

    def test_parse_auditor_json_convenience(self):
        assert parse_auditor_json(_dump()) is not None
        assert parse_auditor_json("乱写") is None

    def test_model_extra_allow_extendable(self):
        # data_shape extendable=True：多余字段不拦
        obj = AuditorOutcome.model_validate(
            {"verdict": "pass", "passed_count": 2, "total_count": 2, "note": "extra"})
        assert obj.verdict == "pass"

    def test_multiple_candidates_prefer_last(self):
        text = ('{"verdict": "block", "passed_count": 0, "total_count": 5} 然后修订为 '
                '{"verdict": "pass", "passed_count": 5, "total_count": 5}')
        payload, errors, _ = parse_auditor_payload(text, prefer="last")
        assert errors == []
        assert payload["verdict"] == "pass"


# ---------------------------------------------------------------------------
# auditor.parse 事件留痕（m02 迁层②：判定解析 emit 进 dispatch.jsonl）
# 复刻 fw-auditor.sh 判尾的 emit 逻辑：判定一旦解析（无论第几层）都要带 meta 留痕，
# 供 runner / 升级链消费。mock 断言：dispatch.jsonl 出现 auditor.parse 事件。
# ---------------------------------------------------------------------------
class TestAuditorParseEvent:
    def _emit(self, log_path, run_id, verdict, parse_meta, module="m02"):
        from fw_runner.events import EventLog
        emeta = dict(parse_meta or {})
        emeta.setdefault("verdict", verdict)
        emeta.setdefault("layer", (4 if verdict in ("block", "parse_failed") else
                                   2 if emeta.get("repaired") else 1))
        EventLog(log_path, run_id=run_id).emit(
            "auditor.parse", module=module, detail=emeta)

    def test_emit_after_llmjson_parse(self, tmp_path):
        # auditor 输出里带脏格式（尾逗号）的结构化判定 → 归一化 layer=2 后 emit
        dirty = ('验收结论：\n```json\n{"verdict": "partial", '
                 '"passed_count": "3", "total_count": "4", '
                 '"remaining_items": ["R3",], }\n```')
        payload, errors, meta = parse_auditor_payload(dirty)
        assert errors == [] and payload is not None
        assert meta["layer"] == 2 and meta["repaired"] is True
        log = tmp_path / "dispatch.jsonl"
        self._emit(log, "r-test", payload["verdict"],
                   {"source": "llmjson", "layer": meta["layer"],
                    "repaired": meta["repaired"], "truncated": meta["truncated"]})
        # mock 断言 dispatch.jsonl 出现 auditor.parse 事件
        lines = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
        evs = [e for e in lines if e.get("event") == "auditor.parse"]
        assert evs, "dispatch.jsonl 应有 auditor.parse 事件"
        assert evs[0]["module"] == "m02"
        assert evs[0]["detail"]["layer"] == 2
        assert evs[0]["detail"]["repaired"] is True
        assert evs[0]["detail"]["verdict"] == "partial"

    def test_emit_marks_failure_layer4(self, tmp_path):
        # 完全解析失败 → 判定不冒充，仍 emit auditor.parse 留痕（layer=4，走回人/parse_failed）
        payload, errors, meta = parse_auditor_payload("auditor 没给 JSON，只说还在跑")
        assert payload is None and meta["layer"] == 4
        log = tmp_path / "dispatch.jsonl"
        self._emit(log, "r-test", "parse_failed", {"source": "text"})
        lines = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
        evs = [e for e in lines if e.get("event") == "auditor.parse"]
        assert evs and evs[0]["detail"]["layer"] == 4
        assert evs[0]["detail"]["verdict"] == "parse_failed"
