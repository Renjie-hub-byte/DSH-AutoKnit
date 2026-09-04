"""fw-normalize 结构化适配层回归测试。

原则：AI 只产内容，程序接管结构。planner 输出宽松 JSON（全角标点/字段错位/
模块 dict 形态/尾逗号），normalize 负责容错解析 + 字段归位 + 标准化输出。

运行：cd framework-v1/fw-tools && python3 -m pytest tests/ -q
"""
import importlib.util
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("fw_normalize", TOOLS / "fw-normalize.py")
fn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fn)


def _mod(mid="m01", **kw):
    m = {"id": mid, "name": "模块", "layer": 1, "objective": "目标",
         "dependencies": [], "acceptance": ["验收"], "boundaries": []}
    m.update(kw)
    return m


def _base(**kw):
    doc = {"task": {"name": "t", "created": "2026-08-25", "grade": "B"}}
    doc.update(kw)
    return doc


# --- 字段归位 ---

def test_clean_json_no_warning():
    d, w = fn.normalize(_base(modules=[_mod()]))
    assert w == []


def test_split_fields_relocated_to_module():
    d, w = fn.normalize(_base(
        first_block={"name": "首发块", "lines": 800},
        remaining_estimate={"lines": 1200, "rounds": 3},
        max_rounds_override=8,
        modules=[_mod()],
    ))
    m0 = d["modules"][0]
    # 顶层注入 + 字段名纠正（name→scope、lines→estimate_lines）+ 丢弃 schema 未定义字段(rounds)
    assert m0["first_block"]["scope"] == "首发块"
    assert m0["first_block"]["estimate_lines"] == 800
    assert m0["remaining_estimate"]["estimate_lines"] == 1200
    assert "rounds" not in m0["remaining_estimate"]
    assert m0["max_rounds_override"] == 8
    # 顶层无残留
    assert not any(k in d for k in ("first_block", "remaining_estimate", "max_rounds_override"))
    assert any("分块字段" in x for x in w)


def test_modules_dict_to_list():
    d, w = fn.normalize(_base(modules={
        "m01": _mod(),
        "m02": _mod("m09", layer=2),
    }))
    ids = [(m["id"], m.get("name")) for m in d["modules"]]
    assert ("m01", "模块") in ids and ("m09", "模块") in ids


def test_loose_budget_runtime_relocated():
    d, w = fn.normalize(_base(max_tokens=100000, executor_max_rounds=5, modules=[_mod()]))
    assert d["budget"]["max_tokens"] == 100000
    assert d["runtime"]["executor_max_rounds"] == 5


def test_unknown_top_level_key_raises():
    with pytest.raises(ValueError, match="未知顶层字段"):
        fn.normalize(_base(hacker_field=1, modules=[]))


def test_module_extra_keys_stripped():
    d, w = fn.normalize(_base(modules=[_mod(hacker_extra=1)]))
    assert "hacker_extra" not in d["modules"][0]


def test_name_fallback_injected():
    # task.name 缺失 → fallback 注入；AI 写了则保留 AI 的
    d, w = fn.normalize(_base(modules=[_mod()]), fallback_name="兜底名")
    assert d["task"]["name"] == "t"
    d2, _ = fn.normalize({"modules": [_mod()]}, fallback_name="兜底名")
    assert d2["task"]["name"] == "兜底名"


def test_empty_modules_raises():
    with pytest.raises(ValueError, match="modules 为空"):
        fn.normalize(_base(modules=[]))


# --- content-mode：结构补全（AI 只填内容，程序补 id/layer/meta/默认值） ---

def test_content_only_full_completion():
    """纯内容输入（无 id/layer/budget/runtime/created）→ 全部补全。"""
    d, w = fn.normalize(
        {"modules": [
            {"name": "数据桥", "objective": "第一步先建桥", "acceptance": ["桥能通"]},
            {"name": "展示层", "objective": "展示", "acceptance": ["能看"],
             "dependencies": ["数据桥"]},
        ]},
        owner="Owner", source_prd="PRD.md", created="2026-08-25")
    m0, m1 = d["modules"]
    assert (m0["id"], m1["id"]) == ("m01", "m02")
    assert (m0["layer"], m1["layer"]) == (1, 2)          # 拓扑推导
    assert m1["dependencies"] == ["m01"]                  # 名字→id 解析
    assert d["task"]["owner"] == "Owner" and d["task"]["created"] == "2026-08-25"
    assert d["budget"]["max_tokens"] == 200000            # 默认注入
    assert d["runtime"]["executor_max_rounds"] == 5
    assert m0["boundaries"] == [] and m0["round_estimate"] == 2


def test_missing_acceptance_raises():
    with pytest.raises(ValueError, match="acceptance"):
        fn.normalize({"modules": [{"name": "m", "objective": "o", "acceptance": []}]})


def test_missing_objective_raises():
    with pytest.raises(ValueError, match="objective"):
        fn.normalize({"modules": [{"name": "m", "acceptance": ["a"]}]})


def test_unknown_dependency_raises():
    with pytest.raises(ValueError, match="不存在"):
        fn.normalize({"modules": [
            {"name": "a", "objective": "o1", "acceptance": ["a1"]},
            {"name": "b", "objective": "o2", "acceptance": ["a2"], "dependencies": ["鬼"]},
        ]})


def test_dependency_cycle_raises():
    with pytest.raises(ValueError, match="依赖环"):
        fn.normalize({"modules": [
            {"name": "a", "objective": "o1", "acceptance": ["a1"], "dependencies": ["b"]},
            {"name": "b", "objective": "o2", "acceptance": ["a2"], "dependencies": ["a"]},
        ]})


def test_three_layer_topology():
    d, _ = fn.normalize({"modules": [
        {"name": "a", "objective": "o", "acceptance": ["x"]},
        {"name": "b", "objective": "o", "acceptance": ["x"], "dependencies": ["a"]},
        {"name": "c", "objective": "o", "acceptance": ["x"], "dependencies": ["b"]},
    ]})
    assert [m["layer"] for m in d["modules"]] == [1, 2, 3]


def test_explicit_id_preserved_layer_recomputed():
    """AI 写了 id → 保留；layer 写错 → 程序按拓扑修正。"""
    d, _ = fn.normalize({"modules": [
        {"id": "x99", "layer": 5, "name": "a", "objective": "o", "acceptance": ["x"]},
    ]})
    assert d["modules"][0]["id"] == "x99"
    assert d["modules"][0]["layer"] == 1


def test_duplicate_id_raises():
    with pytest.raises(ValueError, match="id 重复"):
        fn.normalize({"modules": [
            {"id": "m01", "name": "a", "objective": "o", "acceptance": ["x"]},
            {"id": "m01", "name": "b", "objective": "o", "acceptance": ["x"]},
        ]})


def test_budget_partial_merged():
    """AI 只写部分 budget → 按 key 合并默认，不覆盖已写值。"""
    d, w = fn.normalize({"budget": {"max_tokens": 500},
                         "modules": [{"name": "a", "objective": "o", "acceptance": ["x"]}]})
    assert d["budget"]["max_tokens"] == 500
    assert d["budget"]["warn_at"] == 0.7
    assert any("budget" in x for x in w)


# --- 容错解析 ---

def test_fullwidth_json_parsed():
    raw = '{"task"： {"name"： "t6"}， "modules"： []}'
    d, w = fn._parse_loose(raw)
    assert d["task"]["name"] == "t6"
    assert any("全角" in x for x in w)


def test_tail_comma_and_single_quote_fallback():
    raw = "{'task': {'name': 't7'}, 'modules': [],}"
    d, w = fn._parse_loose(raw)
    assert d["task"]["name"] == "t7"


def test_yaml_fallback():
    raw = "task:\n  name: t8\nmodules: []\n"
    d, w = fn._parse_loose(raw)
    assert d["task"]["name"] == "t8"


def test_garbage_raises():
    with pytest.raises(ValueError):
        fn._parse_loose("this is not json or yaml at all !!!")


# --- 端到端：脏输出 → 标准 task.yaml ---

def test_end_to_end_dirty_output(tmp_path):
    raw = tmp_path / "planner-raw.json"
    raw.write_text(
        '{"task"： {"name"： "脏输出"}， "max_tokens"： 200000，'
        '"first_block"： {"name"： "首发块"}，'
        '"modules"： {"m01"： {"name"： "A"， "layer"： 1， "objective"： "o"，'
        '"dependencies"： []， "acceptance"： ["x"]， "boundaries"： []}}}',
        encoding="utf-8",
    )
    out = tmp_path / "task.yaml"
    rc = fn.main.__wrapped__ if hasattr(fn.main, "__wrapped__") else fn.main
    # 直接调 normalize 流程（不跑 argparse）
    doc, _pw = fn._parse_loose(raw.read_text(encoding="utf-8"))
    doc, _nw = fn.normalize(doc, fallback_name="脏输出")
    import yaml
    with open(out, "w", encoding="utf-8") as f:
        yaml.safe_dump(doc, f, allow_unicode=True, sort_keys=False)
    text = out.read_text(encoding="utf-8")
    assert "first_block" in text
    assert "　" not in text  # 无全角空格
    assert "： " not in text  # 无全角冒号


# --- first_block/remaining_estimate 子结构字段纠正（content-mode 契约版对齐） ---

def test_block_field_aliases_corrected():
    """first_block 写错 name/lines → 纠正 scope/estimate_lines；remaining 丢弃 rounds。"""
    d, w = fn.normalize({"modules": [{
        "name": "A", "objective": "o", "acceptance": ["x"],
        "first_block": {"name": "首发块做X", "lines": 800, "acceptance": ["能跑"]},
        "remaining_estimate": {"lines": 1200, "rounds": 3, "scope": "剩下的"},
    }]})
    fb = d["modules"][0]["first_block"]
    rm = d["modules"][0]["remaining_estimate"]
    assert fb["scope"] == "首发块做X" and fb["estimate_lines"] == 800
    assert "name" not in fb and "lines" not in fb
    assert rm["estimate_lines"] == 1200 and "rounds" not in rm and "lines" not in rm
    assert any("纠正为" in x for x in w)


def test_block_field_missing_scope_warns():
    d, w = fn.normalize({"modules": [{
        "name": "B", "objective": "o", "acceptance": ["x"],
        "first_block": {"estimate_lines": 800},
    }]})
    assert any("缺 scope" in x for x in w)


def test_block_field_correct_not_touched():
    d, w = fn.normalize({"modules": [{
        "name": "C", "objective": "o", "acceptance": ["x"],
        "first_block": {"scope": "做X", "estimate_lines": 800, "acceptance": ["能跑"]},
    }]})
    assert w == []
    assert d["modules"][0]["first_block"]["scope"] == "做X"
