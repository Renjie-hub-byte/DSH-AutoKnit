"""需求1 验收3：合法任务书 → 通过，无告警；默认值套用 + 结构校验。"""
from conftest import make_module, make_task

from fw_protocol import validate_document


def test_valid_task_passes_clean(valid_task):
    result = validate_document(valid_task)
    assert result.status == "pass"
    assert result.ok
    assert result.errors == ()
    assert result.conflicts == ()
    assert result.warnings == ()   # 无告警


def test_defaults_applied(valid_task):
    result = validate_document(valid_task)
    eff = result.effective
    # runtime 显式给了 max_parallel=2，保持；缺失字段补默认
    assert eff["runtime"]["max_parallel"] == 2
    # 显式给的部分不覆盖
    assert eff["budget"]["max_tokens"] == 500000
    # 模块级显式字段不覆盖（m01 显式写了 boundaries）
    m = eff["modules"][0]
    assert m["dependencies"] == []
    assert m["boundaries"] == ['不做数据清洗（只采集落盘）', '不修改上游原始文件']
    assert m["interfaces"] != []
    # 任务书未写的 runtime 字段补默认
    assert eff["runtime"]["executor_max_rounds"] == 5
    assert eff["runtime"]["retry_before_switch"] == 2


def test_defaults_applied_when_section_missing():
    doc = make_task([make_module("m01")])  # 无 budget/runtime/integration
    result = validate_document(doc)
    eff = result.effective
    assert eff["runtime"]["max_parallel"] == 3
    assert eff["runtime"]["end_gate"] == "auto"
    assert eff["runtime"]["models"]["executor"] == "deepseek-v4-flash"
    assert eff["budget"]["max_tokens"] == 1000000
    assert eff["budget"]["warn_at"] == 0.7
    # per_module_max_tokens 缺省 = max_tokens（不单独限制）
    assert eff["budget"]["per_module_max_tokens"] == 1000000
    assert eff["integration"]["contract_file"] == "contracts/api.yaml"
    # 搜索路径里不该有默认值残影
    assert eff["task"]["name"] == "测试任务"


def test_missing_modules_rejected():
    result = validate_document({"task": {"name": "x"}})
    assert result.status == "error"
    assert any(i.code == "schema" for i in result.errors)


def test_unknown_top_level_key_rejected():
    doc = make_task([make_module("m01")], budget={"max_tokens": 100})
    doc["typo_field"] = True
    result = validate_document(doc)
    assert result.status == "error"
    assert any("typo_field" in i.message for i in result.errors)


def test_layer_out_of_range_rejected():
    doc = make_task([make_module("m01", objective="x")])
    doc["modules"][0]["layer"] = 9
    result = validate_document(doc)
    assert result.status == "error"


def test_budget_warn_gt_stop_rejected():
    doc = make_task([make_module("m01")], budget={"max_tokens": 100, "warn_at": 0.9, "stop_at": 0.5})
    result = validate_document(doc)
    assert result.status == "error"
    assert any(i.code == "budget_range_invalid" for i in result.errors)


def test_per_module_gt_global_warns():
    doc = make_task([make_module("m01")], budget={"max_tokens": 100, "per_module_max_tokens": 500})
    result = validate_document(doc)
    # 仅 warning，不阻断
    assert result.status == "pass"
    assert any(i.code == "budget_per_module_gt_global" for i in result.warnings)


def test_module_level_defaults_filled():
    # 模块缺 dependencies/interfaces/boundaries → 默认补 []（供 scaffold 派生时免判空）
    doc = {"task": {"name": "t"},
           "modules": [{"id": "m01", "name": "x", "layer": 1, "objective": "o", "acceptance": ["a"]}]}
    eff = validate_document(doc).effective
    m = eff["modules"][0]
    assert m["dependencies"] == []
    assert m["interfaces"] == []
    assert m["boundaries"] == []
