# -*- coding: utf-8 -*-
"""llmjson 回归测试（BUG-20260903 案例：json-repair + Pydantic 统一解析层）。

样例全部来自真实故障模式：
- 格式病：markdown 围栏 / 尾逗号 / 字符串数字
- 结构漂移：裸 next_block（BUG-20260829）/ 内层字段塞错层 / 空块
- 语义兜底：顿号串 deliverables / cannot_split / 垃圾文本
"""
from __future__ import annotations

import json

import pytest

from fw_runner.llmjson import (
    extract_json_objects,
    loads_llm,
    normalize_split_payload,
    parse_split_json,
    parse_split_payload,
)

pydantic = pytest.importorskip("pydantic")

from fw_runner.llmjson import SplitJSON  # noqa: E402


VALID_SPLIT = {
    "action": "split",
    "parent_module": "m05",
    "next_block": {
        "id": "m05a",
        "name": "下一块",
        "objective": "实现 render/output 编排",
        "deliverables": ["render 阶段接入", "output 阶段接入"],
        "files": ["src/pipeline/runners.py"],
    },
    "remaining_after": {"scope": "Web 控制台", "estimate_lines": 900},
    "dependency_map": {"m05a": []},
    "context_from_parent": "复用 m04 渲染合成",
}


class TestLoadsLlm:
    def test_direct(self):
        assert loads_llm('{"a": 1}') == {"a": 1}

    def test_markdown_fenced(self):
        assert loads_llm('```json\n{"a": 1}\n```') == {"a": 1}

    def test_trailing_comma_repaired(self):
        assert loads_llm('{"a": [1, 2,]}') == {"a": [1, 2]}

    def test_single_quotes_repaired(self):
        assert loads_llm("{'a': 'x'}") == {"a": "x"}

    def test_garbage_returns_none(self):
        assert loads_llm("这不是 JSON，只是随便一段话") is None
        assert loads_llm("") is None
        assert loads_llm(None) is None


class TestExtractJsonObjects:
    def test_mixed_text_extracts_dict(self):
        text = '前置说明一段\n```json\n{"action": "split", "note": "x"}\n```\n后置补充'
        objs = extract_json_objects(text)
        assert objs and objs[0]["action"] == "split"

    def test_broken_json_repaired_not_dropped(self):
        # 尾逗号：旧正则方案 json.loads 失败静默丢弃 → 现在修复成功
        text = '{"action": "cannot_split", "reason": "剩余不多",}'
        assert extract_json_objects(text)[0]["action"] == "cannot_split"

    def test_multiple_candidates(self):
        text = '{"n": 1} 中间 {"action": "split"}'
        objs = extract_json_objects(text)
        assert len(objs) == 2


class TestSplitJSONModel:
    def test_valid(self):
        m = SplitJSON.model_validate(dict(VALID_SPLIT))
        assert m.action == "split"
        assert m.next_block.id == "m05a"

    def test_string_lines_coerced(self):
        d = json.loads(json.dumps(VALID_SPLIT))
        d["remaining_after"]["estimate_lines"] = "900"
        assert SplitJSON.model_validate(d).remaining_after.estimate_lines == 900

    def test_chinese_number_lines_coerced(self):
        d = json.loads(json.dumps(VALID_SPLIT))
        d["remaining_after"]["estimate_lines"] = "约900行"
        assert SplitJSON.model_validate(d).remaining_after.estimate_lines == 900

    def test_deliverables_dunhao_string_coerced(self):
        d = json.loads(json.dumps(VALID_SPLIT))
        d["next_block"]["deliverables"] = "render 接入；output 接入、字幕"
        assert SplitJSON.model_validate(d).next_block.deliverables == [
            "render 接入", "output 接入、字幕",
        ] or len(SplitJSON.model_validate(d).next_block.deliverables) >= 2

    def test_cannot_split_minimal(self):
        m = SplitJSON.model_validate({"action": "cannot_split"})
        assert m.action == "cannot_split" and m.next_block is None

    def test_empty_block_to_none(self):
        d = json.loads(json.dumps(VALID_SPLIT))
        d["action"] = "cannot_split"
        d["next_block"] = {}
        assert SplitJSON.model_validate(d).next_block is None

    def test_missing_id_rejected(self):
        d = json.loads(json.dumps(VALID_SPLIT))
        del d["next_block"]["id"]
        with pytest.raises(pydantic.ValidationError):
            SplitJSON.model_validate(d)


class TestNormalizeAndParse:
    def test_bare_next_block_wrapped(self):
        bare = dict(VALID_SPLIT["next_block"])
        out = normalize_split_payload(bare, "m05")
        assert out["action"] == "split" and out["parent_module"] == "m05"

    def test_nested_fields_promoted(self):
        d = json.loads(json.dumps(VALID_SPLIT))
        d["next_block"]["remaining_after"] = d.pop("remaining_after")
        out = normalize_split_payload(d, "m05")
        assert "remaining_after" in out and "remaining_after" not in out["next_block"]

    def test_parse_split_json_end_to_end_dirty_text(self):
        """截断必须**完整**修复——不能捞个内层碎片 + 默认值冒充成功（历史假绿）。"""
        text = (
            "好的，以下是拆解：\n```json\n"
            + json.dumps(VALID_SPLIT, ensure_ascii=False)[:-1]  # 故意截掉尾大括号
            + "\n```\n请查收"
        )
        out = parse_split_json(text, "m05")
        assert out is not None and out["action"] == "split"
        # 三条硬断言钉死"完整"：块本体 + 剩余量数值 + 依赖，缺一即回退成假绿
        assert out["next_block"]["id"] == VALID_SPLIT["next_block"]["id"]
        assert out["remaining_after"]["estimate_lines"] == \
            VALID_SPLIT["remaining_after"]["estimate_lines"]
        assert out["dependency_map"] == VALID_SPLIT["dependency_map"]

    def test_truncated_salvage_is_recorded_in_meta(self):
        """截断走的是层④ repair，必须留痕（salvaged_truncated）。"""
        text = json.dumps(VALID_SPLIT, ensure_ascii=False)[:-1]
        _payload, _errors, meta = parse_split_payload(text, "m05")
        assert meta["truncated"] is True and meta["salvaged_truncated"] is True
        assert meta["layer"] == 2 and meta["repaired"] is True

    def test_missing_remaining_after_rejected_not_defaulted(self):
        """P0-4：漏写 remaining_after 必须**拒**，不能被默认成 0。

        0 = "没有剩余" = 子模块做完首发块直接 done = 父模块剩下的活凭空消失且零报错。
        旧手写路径靠 REQUIRED_TOP 响亮拒绝，Pydantic 迁移时丢了这道闸。
        """
        from fw_runner.split import validate_split_json
        data = {
            "action": "split", "parent_module": "m05",
            "next_block": {"id": "m05a", "name": "n", "objective": "o",
                           "deliverables": ["d"], "files": []},
            "dependency_map": {},        # 注意：没有 remaining_after
        }
        ok, errors = validate_split_json(data)
        assert not ok, "缺 remaining_after 竟被放行 → 会静默丢活"
        assert any("remaining_after" in e for e in errors)
        assert data["next_block"]["id"] == "m05a"      # 拒绝时不得改动原数据

    def test_explicit_zero_remaining_still_accepted(self):
        """与上一条配对：显式写 0（收尾块）必须放行，别把两种语义一起堵死。"""
        from fw_runner.split import validate_split_json
        data = {
            "action": "split", "parent_module": "m05",
            "next_block": {"id": "m05w", "name": "n", "objective": "o",
                           "deliverables": ["d"], "files": []},
            "remaining_after": {"scope": "", "estimate_lines": 0},   # 显式收尾块
            "dependency_map": {},
        }
        ok, errors = validate_split_json(data)
        assert ok, errors
        assert data["remaining_after"]["estimate_lines"] == 0

    def test_parse_split_json_garbage_none(self):
        assert parse_split_json("完全没有 JSON 的输出", "m05") is None

    def test_validate_split_json_pyantic_path(self):
        from fw_runner.split import validate_split_json
        ok, errors = validate_split_json(dict(VALID_SPLIT))
        assert ok and not errors
        ok2, errors2 = validate_split_json({"action": "cannot_split", "reason": "一轮能完"})
        assert not ok2 and errors2 and "cannot_split" in errors2[0]


# ---------------------------------------------------------------------------
# P0 复查回归（2026-09-04 小澈）：第二层的产出必须是「归一化后的对象」，
# 不是「通过/失败」布尔值——coercion 不写回 = 校验放行、下游照旧读到脏数据。
# ---------------------------------------------------------------------------

class TestCoercionWriteBack:
    """validate_split_json 原地修正 data（第二层 → 业务逻辑的交接面）。"""

    def test_deliverables_sep_string_written_back_as_list(self):
        from fw_runner.split import validate_split_json
        data = {
            "action": "split", "parent_module": "m05",
            "next_block": {"id": "m05a", "name": "解析子块", "objective": "做解析",
                           "deliverables": "解析脚本、渲染脚本、导出脚本", "files": ["a.py"]},
            "remaining_after": {"scope": "剩余渲染", "estimate_lines": "约900行"},
            "dependency_map": {"m05a": ["m04"]},
        }
        ok, errors = validate_split_json(data)
        assert ok, errors
        # 关键断言：下游读到的是修正后的列表，不是原串
        assert data["next_block"]["deliverables"] == ["解析脚本", "渲染脚本", "导出脚本"]
        assert data["remaining_after"]["estimate_lines"] == 900

    def test_no_char_iteration_in_taskbook(self):
        """P0-1 的真实伤害面：原串过校验后被 [str(x) for x in <str>] 按字符迭代。"""
        from fw_runner.split import validate_split_json
        data = {
            "action": "split", "parent_module": "m05",
            "next_block": {"id": "m05a", "name": "n", "objective": "o",
                           "deliverables": "甲、乙", "files": []},
            "remaining_after": {"scope": "s"}, "dependency_map": {},
        }
        assert validate_split_json(data)[0]
        dls = data["next_block"]["deliverables"]
        assert isinstance(dls, list) and dls == ["甲", "乙"]
        assert len([str(x) for x in dls]) == 2          # 不再是 3 个字符

    def test_scope_null_accepted(self):
        """P0-2：收尾块高频写法 "scope": null —— 旧手写路径放过，不能比改造前更严。"""
        from fw_runner.split import validate_split_json
        data = {
            "action": "split", "parent_module": "m05",
            "next_block": {"id": "m05a", "name": "n", "objective": "o",
                           "deliverables": ["d"], "files": []},
            "remaining_after": {"scope": None, "estimate_lines": 0},
            "dependency_map": {},
        }
        ok, errors = validate_split_json(data)
        assert ok, errors
        assert data["remaining_after"]["scope"] == ""

    def test_depmap_string_value_accepted(self):
        """P0-2：dependency_map 值写成裸串 {"m05a": "m04"} 是 LLM 高频写法。"""
        from fw_runner.split import validate_split_json
        data = {
            "action": "split", "parent_module": "m05",
            "next_block": {"id": "m05a", "name": "n", "objective": "o",
                           "deliverables": ["d"], "files": []},
            "remaining_after": {"scope": "剩余", "estimate_lines": 300},
            "dependency_map": {"m05a": "m04"},
        }
        ok, errors = validate_split_json(data)
        assert ok, errors
        assert data["dependency_map"] == {"m05a": ["m04"]}

    def test_failed_validation_leaves_data_untouched(self):
        """校验失败绝不能把 data 清空（先校验成功再替换）。"""
        from fw_runner.split import validate_split_json
        data = {
            "action": "split", "parent_module": "m05",
            "next_block": {"id": "", "name": "n", "objective": "o",
                           "deliverables": ["d"], "files": []},
            "remaining_after": {"scope": "剩余"}, "dependency_map": {},
        }
        snapshot = json.dumps(data, ensure_ascii=False, sort_keys=True)
        ok, errors = validate_split_json(data)
        assert not ok and errors
        assert json.dumps(data, ensure_ascii=False, sort_keys=True) == snapshot

    def test_extra_fields_survive_writeback(self):
        """extra="allow" 的字段不能被 model_dump 写回时丢掉（协议外扩展要透传）。"""
        from fw_runner.split import validate_split_json
        data = {
            "action": "split", "parent_module": "m05",
            "next_block": {"id": "m05a", "name": "n", "objective": "o",
                           "deliverables": ["d"], "files": [], "est_lines": 800},
            "remaining_after": {"scope": "剩余", "note": "留给下块"},
            "dependency_map": {},
        }
        assert validate_split_json(data)[0]
        assert data["next_block"]["est_lines"] == 800
        assert data["remaining_after"]["note"] == "留给下块"
