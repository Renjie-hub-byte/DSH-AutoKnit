"""轮数预判补丁验收：round_estimate / max_rounds_override 校验规则。

- ① round_estimate > 上限×2 → error（强制切开）
- ② round_estimate > 上限   → warning（建议切开，不阻断）
- ③ 合法（≤ 上限）          → 通过，无轮数相关告警
- 附带：max_rounds_override 默认继承 runtime.executor_max_rounds；
       round_estimate < 1 被 schema 拦下。
"""
from conftest import make_module, make_task

from fw_protocol import validate_document

TOO_LARGE = "module_round_estimate_too_large"
OVER_CAP = "module_round_estimate_over_cap"


def _task_with(round_estimate=None, max_rounds_override=None, runtime_rounds=None, mid="m01"):
    m = make_module(mid)
    if round_estimate is not None:
        m["round_estimate"] = round_estimate
    if max_rounds_override is not None:
        m["max_rounds_override"] = max_rounds_override
    overrides = {}
    if runtime_rounds is not None:
        overrides["runtime"] = {"executor_max_rounds": runtime_rounds}
    return make_task([m], **overrides)


def test_round_estimate_over_twice_cap_is_error():
    """① 预估 > 上限×2 → error（强制切开）。"""
    result = validate_document(_task_with(round_estimate=11, max_rounds_override=5))
    assert result.status == "error"
    assert not result.ok
    assert any(i.code == TOO_LARGE and i.module_id == "m01" for i in result.errors)


def test_round_estimate_over_cap_warns():
    """② 预估 > 上限 → warning（status 仍 pass，不阻断）。"""
    result = validate_document(_task_with(round_estimate=6, max_rounds_override=5))
    assert result.status == "pass"
    assert result.ok
    assert result.errors == ()
    assert any(i.code == OVER_CAP and i.module_id == "m01" for i in result.warnings)


def test_round_estimate_within_cap_passes_clean():
    """③ 合法：预估 ≤ 上限 → 通过，无轮数相关 issue。"""
    result = validate_document(_task_with(round_estimate=5, max_rounds_override=5))
    assert result.status == "pass"
    assert result.ok
    assert not any(i.code in (TOO_LARGE, OVER_CAP) for i in result.all_issues)


def test_round_estimate_at_boundaries():
    """边界：预估 == 上限 → 完全通过；== 上限×2 → 仅 warning 不 error（严格 > 才升级）。"""
    r1 = validate_document(_task_with(round_estimate=5, max_rounds_override=5))
    assert r1.status == "pass"
    assert not any(i.code in (TOO_LARGE, OVER_CAP) for i in r1.all_issues)
    r2 = validate_document(_task_with(round_estimate=10, max_rounds_override=5))
    assert r2.status == "pass"  # 仅 warning，不阻断
    assert not any(i.code == TOO_LARGE for i in r2.all_issues)
    assert any(i.code == OVER_CAP for i in r2.warnings)


def test_max_rounds_override_defaults_to_runtime():
    """max_rounds_override 缺省继承 runtime.executor_max_rounds（effective 可见）。"""
    rt = make_task([make_module("m01")], runtime={"executor_max_rounds": 3})
    eff = validate_document(rt).effective
    assert eff["modules"][0]["max_rounds_override"] == 3
    # runtime 也缺省时，继承默认 5
    eff2 = validate_document(make_task([make_module("m01")])).effective
    assert eff2["modules"][0]["max_rounds_override"] == 5
    assert eff2["runtime"]["executor_max_rounds"] == 5


def test_max_rounds_override_explicit_wins():
    """显式 max_rounds_override 优先于 runtime 继承值。"""
    m = make_module("m01")
    m["max_rounds_override"] = 8
    eff = validate_document(make_task([m], runtime={"executor_max_rounds": 5})).effective
    assert eff["modules"][0]["max_rounds_override"] == 8


def test_round_estimate_zero_rejected_by_schema():
    """round_estimate 必须 ≥ 1：0 被 schema 拦下（结构 error）。"""
    m = make_module("m01")
    m["round_estimate"] = 0
    result = validate_document(make_task([m]))
    assert result.status == "error"
    assert any(i.code == "schema" for i in result.errors)


def test_missing_round_estimate_no_warning():
    """未填 round_estimate：不参与轮数预判校验（向后兼容旧任务书），also 不注入 None 默认（可安全落盘）。"""
    result = validate_document(make_task([make_module("m01")]))
    assert result.status == "pass"
    assert not any(i.code in (TOO_LARGE, OVER_CAP) for i in result.all_issues)
    m = result.effective["modules"][0]
    assert "round_estimate" not in m  # 未预估 = 键缺席
    assert m["max_rounds_override"] == 5  # 但上限继承仍补全，供下游读取