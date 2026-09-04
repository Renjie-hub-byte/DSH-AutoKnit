"""v2 split 贪心单块落地（2026-08-26 对齐 prompts/split.md）：
D1 收集上下文 / D2 调 split agent + 拆解 JSON 校验 / D3 scaffold next_block 子模块 /
D4 SHARED_CONTEXT.md / D5 子模块入队 + 依赖图（D1–D5）。

v2 协议 = next_block 单块 + remaining_after（递归），区别于 v1 的 new_modules 2-3 个。
全部用 fw-scaffold 生成真实目录树 + load_task_context，driver 用 InlineAgentDriver 模拟。
"""
from __future__ import annotations

import json

import pytest

from fw_runner.context import load_task_context
from fw_runner.drivers import InlineAgentDriver
from fw_runner.model import DriverOutcome, RunState
from fw_runner.review import append_done, read_review
from fw_runner.split import (
    SHARED_CONTEXT_NAME,
    SPLIT_CONTEXT_REL,
    CannotSplitError,
    SplitCallError,
    SplitInfraError,
    SplitJSONError,
    build_wrapup_split_json,
    call_split_agent,
    collect_split_context,
    generate_shared_context,
    insert_children_into_order,
    scaffold_children,
    validate_split_json,
    _resolve_protocol_retries,
)

from helpers import build_task  # noqa: E402

VALID_SPLIT = {
    "action": "split",
    "parent_module": "m02",
    "next_block": {
        "id": "m02a",
        "name": "核心算法",
        "objective": "[实现后端 API] m02a 第一步实现核心算法",
        "deliverables": ["算法实现", "算法单测"],
        "files": ["src/algorithm.py"],
    },
    "remaining_after": {
        "scope": "数据预处理；API 接口；单元测试",
        "estimate_lines": 600,
    },
    "dependency_map": {"m02a": ["m01"]},
    "context_from_parent": "src/algorithm.py 已完成框架；未完成预处理管线",
}


def _mod(id_: str, name: str, objective: str = "目标", deps=None, acceptance=None):
    return {
        "id": id_, "name": name, "layer": 1, "objective": objective,
        "dependencies": deps or [],
        "interfaces": [{"path": f"/api/{id_}/*", "method": ["GET"], "note": f"{id_} 接口"}],
        "acceptance": acceptance or [f"{id_} 验收：按 contract.yaml 产出 src 产物"],
        "boundaries": [f"{id_} 不跨界"],
    }


@pytest.fixture
def split_root(tmp_path):
    """m01 → m02（待拆，4 项交付物）→ m03；m02 有 REVIEW 已做 + src 产物。"""
    mods = [
        _mod("m01", "上游", objective="上游目标", acceptance=["m01 a", "m01 b"]),
        _mod("m02", "后端API", objective="实现后端 API", deps=["m01"],
             acceptance=["核心算法", "数据预处理", "API 接口", "单元测试"]),
        _mod("m03", "下游", objective="下游目标", deps=["m02"]),
    ]
    root = build_task(tmp_path, "验收D-split-v2", mods, runtime={"max_parallel": 2,
                                                                 "executor_max_rounds": 8,
                                                                 "retry_before_switch": 2,
                                                                 "max_executor_switches": 1,
                                                                 "end_gate": "auto"})
    m02 = root / "modules" / "m02-后端API"
    append_done(m02 / "REVIEW.md", "核心算法框架已完成")
    (m02 / "src" / "algorithm.py").write_text("# done\n", encoding="utf-8")
    return root


def _ctx_and_state(root):
    ctx = load_task_context(root)
    state = RunState()
    for mid in ctx.module_order:
        state.modules[mid] = "pending"
        state.ensure(mid)
    return ctx, state


# ---------- 拆解 JSON 校验（v2 协议） ----------

def test_validate_split_json_valid():
    ok, errors = validate_split_json(VALID_SPLIT)
    assert ok is True
    assert errors == []


def test_validate_split_json_missing_fields():
    ok, errors = validate_split_json({"action": "split", "next_block": {"id": "x"}})
    assert ok is False
    joined = "; ".join(errors)
    assert "parent_module" in joined
    assert "next_block" in joined
    assert "remaining_after" in joined
    assert "dependency_map" in joined


def test_validate_split_json_cannot_split():
    ok, errors = validate_split_json({"action": "cannot_split", "reason": "已是叶子模块"})
    assert ok is False
    assert "cannot_split" in errors[0]
    assert "叶子" in errors[0]


def test_validate_split_json_bad_action():
    ok, errors = validate_split_json({"action": "other"})
    assert ok is False
    assert "action 必须是 'split'" in errors[0]


def test_validate_split_json_not_a_dict():
    ok, errors = validate_split_json(["not", "dict"])
    assert ok is False


def test_validate_split_json_next_block_missing_fields():
    """2026-09-04 行为变更（llmjson/Pydantic 接管）：缺字段仍拒绝（关键语义字段硬约束），
    报错文案从「next_block.X 缺失」变为 Pydantic 字段级错误（含 objective/deliverables 字段名）。"""
    data = dict(VALID_SPLIT)
    data["next_block"] = {"id": "m02a", "name": "核心算法"}
    ok, errors = validate_split_json(data)
    assert ok is False
    joined = "; ".join(errors)
    assert "objective" in joined
    assert "deliverables" in joined


def test_validate_split_json_remaining_after_bad():
    """2026-09-04 行为变更（llmjson 容错）：坏行数估计（"abc"）coercion 到 0 放行——
    行数只是报表量，不再炸拆分链路（BUG-20260903 案例教训）。"""
    data = dict(VALID_SPLIT)
    data["remaining_after"] = {"scope": "", "estimate_lines": "abc"}
    ok, errors = validate_split_json(data)
    assert ok is True and not errors


def test_validate_split_json_wrapup_block_allowed():
    """单块语义（2026-08-28 Owner定稿）：remaining_after 空 = 剩余全量一块下放，做完即 done。"""
    data = dict(VALID_SPLIT)
    data["remaining_after"] = {"scope": "", "estimate_lines": 0}
    ok, errors = validate_split_json(data)
    assert ok is True, errors
    data2 = dict(VALID_SPLIT)
    data2["remaining_after"] = {}
    ok2, errors2 = validate_split_json(data2)
    assert ok2 is True, errors2


# ---------- D1 collect_split_context ----------

def test_collect_split_context_inputs(split_root):
    ctx, state = _ctx_and_state(split_root)
    audit = {"passed_count": 1, "total_count": 4,
             "remaining_items": ["数据预处理", "API 接口", "单元测试"]}
    c = collect_split_context(ctx, state, "m02", audit=audit)
    assert c["mid"] == "m02"
    assert c["objective"] == "实现后端 API"
    assert c["deliverables"] == ["核心算法", "数据预处理", "API 接口", "单元测试"]
    assert c["passed_count"] == 1
    assert c["total_count"] == 4
    assert c["remaining_items"] == ["数据预处理", "API 接口", "单元测试"]
    # U2（BUG-20260904）：audit 真实传入 → remaining_unknown=False；缺席 → True
    assert c["remaining_unknown"] is False
    c_none = collect_split_context(ctx, state, "m02", audit=None)
    assert c_none["remaining_unknown"] is True
    assert "核心算法框架已完成" in c["review"]
    assert c["review_done"] == ["核心算法框架已完成"]
    assert any(p.endswith("src/algorithm.py") for p in c["files"])
    assert c["dependencies"] == ["m01"]
    # v2 新增：总目标层（task_goal / will_not_have / module_remaining 缺省兼容）
    assert "task_goal" in c
    assert "will_not_have" in c
    # M1（7a）：module_remaining 动态化——REVIEW done=1；静态 estimate_lines 缺失
    # （None）→ 不按占比计算，回退 static_fallback 并留痕
    mr = c["module_remaining"]
    assert mr["source"] == "static_fallback"
    assert mr["review_done_count"] == 1
    assert isinstance(mr["scope"], str) and mr["estimate_lines"] is None


def test_collect_split_context_audit_from_driveroutcome(split_root):
    ctx, state = _ctx_and_state(split_root)
    aout = DriverOutcome(status="ok", verdict="partial", passed_count=2, total_count=4,
                         remaining_items=["API 接口", "单元测试"])
    c = collect_split_context(ctx, state, "m02", audit=aout)
    assert c["audit"]["verdict"] == "partial"
    assert c["passed_count"] == 2
    assert c["total_count"] == 4
    assert c["remaining_items"] == ["API 接口", "单元测试"]


# ---------- D2 call_split_agent ----------

def _inline_split_driver(split_json=VALID_SPLIT, status="ok"):
    def fn(ctx):
        return DriverOutcome(status=status, detail={"split": split_json})
    return InlineAgentDriver(fn)


def _capture_env_split_driver(captured, split_json=VALID_SPLIT):
    def fn(ctx):
        captured["FW_SPLIT_MODEL"] = ctx.env.get("FW_SPLIT_MODEL")
        return DriverOutcome(status="ok", detail={"split": split_json})
    return InlineAgentDriver(fn)


def test_call_split_agent_ok(split_root):
    ctx, state = _ctx_and_state(split_root)
    context = collect_split_context(ctx, state, "m02",
                                    audit={"passed_count": 1, "total_count": 4})
    result = call_split_agent(ctx, "m02", context, driver=_inline_split_driver())
    assert result["action"] == "split"
    assert result["next_block"]["id"] == "m02a"
    assert "remaining_after" in result
    assert result["remaining_after"]["estimate_lines"] == 600


def test_call_split_agent_injects_real_model_name(split_root):
    """FW_SPLIT_MODEL 必须是真实模型名（deepseek-v4-flash），不是档位名 flash。"""
    ctx, state = _ctx_and_state(split_root)
    assert ctx.config.model_tiers == ["flash", "pro"]
    captured = {}
    context = collect_split_context(ctx, state, "m02")
    call_split_agent(ctx, "m02", context, driver=_capture_env_split_driver(captured))
    assert captured["FW_SPLIT_MODEL"] == "deepseek-v4-flash"
    assert captured["FW_SPLIT_MODEL"] != "flash"


def test_call_split_agent_ignores_custom_tier_names(split_root):
    ctx, state = _ctx_and_state(split_root)
    ctx.config.model_tiers = ["myflash", "mypro"]
    captured = {}
    context = collect_split_context(ctx, state, "m02")
    call_split_agent(ctx, "m02", context, driver=_capture_env_split_driver(captured))
    assert captured["FW_SPLIT_MODEL"] == "deepseek-v4-flash"


def test_call_split_agent_writes_context_file(split_root):
    ctx, state = _ctx_and_state(split_root)
    context = collect_split_context(ctx, state, "m02", audit={"passed_count": 1, "total_count": 4})
    call_split_agent(ctx, "m02", context, driver=_inline_split_driver())
    p = ctx.modules["m02"].dir / SPLIT_CONTEXT_REL
    assert p.is_file()
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["mid"] == "m02"
    assert data["total_count"] == 4
    assert "task_goal" in data


def test_call_split_agent_invalid_json_raises(split_root):
    ctx, state = _ctx_and_state(split_root)
    bad = {"action": "split", "next_block": {"id": "x"}}  # 缺 remaining_after/dependency_map
    context = collect_split_context(ctx, state, "m02")
    with pytest.raises(SplitJSONError):
        call_split_agent(ctx, "m02", context, driver=_inline_split_driver(bad))


def test_call_split_agent_cannot_split_raises(split_root):
    """cannot_split → CannotSplitError（业务分支，runner 程序化生成单块下放，2026-08-28）。"""
    ctx, state = _ctx_and_state(split_root)
    cannot = {"action": "cannot_split", "reason": "模块已是叶子"}
    with pytest.raises(CannotSplitError):
        call_split_agent(ctx, "m02", collect_split_context(ctx, state, "m02"),
                         driver=_inline_split_driver(cannot))


def test_build_wrapup_split_json_single_block(split_root):
    """CannotSplitError 兜底：程序化生成「单块」（剩余全量 + remaining_after 空），
    next_block.deliverables 优先取 auditor remaining_items。"""
    ctx, state = _ctx_and_state(split_root)
    context = collect_split_context(
        ctx, state, "m02",
        audit={"passed_count": 2, "total_count": 4,
               "remaining_items": ["验收3：数据预处理", "验收4：API 接口"]})
    sj = build_wrapup_split_json(ctx, "m02", context)
    assert sj["action"] == "split"
    assert sj["parent_module"] == "m02"
    nb = sj["next_block"]
    assert nb["id"] == "m02w"
    assert nb["objective"].startswith("【")
    assert nb["deliverables"] == ["验收3：数据预处理", "验收4：API 接口"]
    assert nb["files"] == []
    assert sj["remaining_after"] == {"scope": "", "estimate_lines": 0}
    assert sj["dependency_map"] == {"m02w": []}


def test_build_wrapup_split_json_falls_back_review_todo(split_root):
    """remaining_items 空 → 回落 REVIEW 待办；再空 → scope 兜底。"""
    ctx, state = _ctx_and_state(split_root)
    context = collect_split_context(ctx, state, "m02",
                                    audit={"passed_count": 2, "total_count": 4,
                                           "remaining_items": []})
    context["review_todo"] = ["补数据预处理", "补 API 接口"]
    sj = build_wrapup_split_json(ctx, "m02", context)
    assert sj["next_block"]["deliverables"] == ["补数据预处理", "补 API 接口"]
    context2 = collect_split_context(ctx, state, "m02")
    context2["remaining_items"] = []
    context2["review_todo"] = []
    context2["module_remaining"] = {"scope": "剩余的导出管线", "estimate_lines": 400}
    sj2 = build_wrapup_split_json(ctx, "m02", context2)
    assert sj2["next_block"]["deliverables"] == ["完成剩余工作：剩余的导出管线"]


def test_call_split_agent_error_status_raises(split_root):
    ctx, state = _ctx_and_state(split_root)
    with pytest.raises(SplitCallError):
        call_split_agent(ctx, "m02", collect_split_context(ctx, state, "m02"),
                         driver=_inline_split_driver(status="error"))


# ---------- D3 scaffold_children（v2：next_block 单块） ----------

def test_scaffold_children_standard_structure(split_root):
    ctx, state = _ctx_and_state(split_root)
    child_ids = scaffold_children(ctx, "m02", VALID_SPLIT)
    assert child_ids == ["m02a"]
    d = ctx.modules["m02a"].dir
    assert d.parent == ctx.modules["m02"].dir.parent       # modules/ 平级
    assert d.is_dir()
    for rel in ("src", "test", "logs", "tmp"):
        assert (d / rel).is_dir()
    for rel in ("REVIEW.md", "contract.yaml", "任务书-m02a.yaml", "交付说明.md"):
        assert (d / rel).is_file(), rel
    assert (d / "src" / ".gitkeep").is_file()
    assert (d / "logs" / ".auditor-ignore").is_file()


def test_scaffold_children_registers_specs(split_root):
    ctx, state = _ctx_and_state(split_root)
    scaffold_children(ctx, "m02", VALID_SPLIT)
    spec = ctx.modules["m02a"]
    assert spec.id == "m02a"
    assert spec.layer == 2
    assert spec.dependencies == ["m01"]
    assert spec.review_path.is_file()
    doc = read_review(spec.review_path)
    assert doc.kv.get("status") == "pending"
    assert doc.kv.get("split_parent") == "m02"
    book = spec.book_path.read_text(encoding="utf-8")
    assert "m02a" in book
    assert "核心算法" in book


def test_scaffold_children_duplicate_id_raises(split_root):
    ctx, state = _ctx_and_state(split_root)
    scaffold_children(ctx, "m02", VALID_SPLIT)
    with pytest.raises(SplitJSONError):
        scaffold_children(ctx, "m02", VALID_SPLIT)   # m02a 已注册


# ---------- D4 generate_shared_context ----------

def test_generate_shared_context_writes_file(split_root):
    ctx, state = _ctx_and_state(split_root)
    scaffold_children(ctx, "m02", VALID_SPLIT)
    path = generate_shared_context(ctx, "m02", VALID_SPLIT)
    assert path == ctx.modules["m02"].dir / SHARED_CONTEXT_NAME
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "children: m02a" in text
    assert "context_from_parent: src/algorithm.py 已完成框架" in text
    assert "algorithm.py" in text          # 已完成文件列表
    assert "核心算法框架已完成" in text     # 已通过功能点 / REVIEW 摘要


# ---------- D5 insert_children_into_order ----------

def test_insert_children_into_order_after_parent(split_root):
    ctx, state = _ctx_and_state(split_root)
    child_ids = scaffold_children(ctx, "m02", VALID_SPLIT)
    insert_children_into_order(ctx, "m02", child_ids, VALID_SPLIT["dependency_map"])
    assert ctx.module_order == ["m01", "m02", "m02a", "m03"]
    assert ctx.dependencies["m02a"] == ["m01"]


def test_insert_children_into_order_inherits_parent_deps_and_excludes_parent(split_root):
    ctx, state = _ctx_and_state(split_root)
    data = dict(VALID_SPLIT)
    data["dependency_map"] = {"m02a": ["m01", "m02"]}   # 故意引用父模块 → 必须剔除（防环）
    child_ids = scaffold_children(ctx, "m02", data)
    insert_children_into_order(ctx, "m02", child_ids, data["dependency_map"])
    assert ctx.dependencies["m02a"] == ["m01"]
    assert "m02" not in ctx.dependencies["m02a"]


def test_insert_children_into_order_idempotent(split_root):
    ctx, state = _ctx_and_state(split_root)
    child_ids = scaffold_children(ctx, "m02", VALID_SPLIT)
    insert_children_into_order(ctx, "m02", child_ids, VALID_SPLIT["dependency_map"])
    insert_children_into_order(ctx, "m02", child_ids, VALID_SPLIT["dependency_map"])
    assert ctx.module_order == ["m01", "m02", "m02a", "m03"]


def test_insert_children_into_order_missing_spec_raises(split_root):
    ctx, state = _ctx_and_state(split_root)
    with pytest.raises(SplitJSONError):
        insert_children_into_order(ctx, "m02", ["m02a"], VALID_SPLIT["dependency_map"])


# ---------- 端到端流水线（D1→D5 一次串起来，v2 单块） ----------

def test_full_split_pipeline(split_root):
    ctx, state = _ctx_and_state(split_root)
    context = collect_split_context(ctx, state, "m02",
                                    audit={"passed_count": 1, "total_count": 4,
                                           "remaining_items": ["数据预处理", "API 接口", "单元测试"]})
    split_json = call_split_agent(ctx, "m02", context, driver=_inline_split_driver())
    child_ids = scaffold_children(ctx, "m02", split_json)
    insert_children_into_order(ctx, "m02", child_ids, split_json["dependency_map"])
    generate_shared_context(ctx, "m02", split_json)

    assert ctx.module_order == ["m01", "m02", "m02a", "m03"]
    assert "m02a" in ctx.modules
    assert ctx.dependencies["m02a"] == ["m01"]
    shared = ctx.modules["m02"].dir / SHARED_CONTEXT_NAME
    assert shared.is_file()
    # 子模块目录在 modules/ 平级，父模块目录仍存在（标记 split 容器）
    assert ctx.modules["m02a"].dir.parent == ctx.modules["m02"].dir.parent
    # remaining_after 透传进 split-outcome（递归基础）
    assert split_json["remaining_after"]["estimate_lines"] == 600

# ---------- 层③ 协议重试回喂 / 故障分类（2026-09-04 小澈复查四条落地） ----------

def _counting_split_driver(payloads, parse_meta=None):
    """按次返回拆解 JSON（列表用尽后重复最后一个），并记录调用次数与收到的 context。"""
    calls = {"n": 0, "contexts": []}

    def fn(ctx):
        calls["n"] += 1
        import json as _json
        calls["contexts"].append(_json.loads(
            (ctx.module.dir / SPLIT_CONTEXT_REL).read_text(encoding="utf-8")))
        payload = payloads[min(calls["n"], len(payloads)) - 1]
        detail = {"split": payload}
        if parse_meta:
            detail["_parse"] = dict(parse_meta)
        return DriverOutcome(status="ok", detail=detail)
    return InlineAgentDriver(fn), calls


def test_split_agent_retries_and_feeds_back_errors(split_root):
    """层③：协议故障 → 字段级错误写回 context 供 fw-split.sh 回喂，第二次成功。"""
    ctx, state = _ctx_and_state(split_root)
    bad = dict(VALID_SPLIT, next_block={**VALID_SPLIT["next_block"], "id": ""})
    good = VALID_SPLIT
    driver, calls = _counting_split_driver([bad, good])
    events = []
    out = call_split_agent(ctx, "m02", collect_split_context(ctx, state, "m02"), driver=driver,
                           on_event=lambda k, d: events.append((k, d)))
    assert calls["n"] == 2
    assert out["next_block"]["id"] == "m02a"
    assert "protocol_errors" not in calls["contexts"][0]          # 首试不带反馈
    assert calls["contexts"][1]["protocol_errors"]                # 回喂轮带字段级错误
    assert any("next_block.id" in e for e in calls["contexts"][1]["protocol_errors"])
    assert [k for k, _ in events] == ["split.protocol_retry", "split.parse"]
    assert events[-1][1]["attempt"] == 2 and events[-1][1]["retries_used"] == 1


def test_split_agent_exhausts_retries_then_raises(split_root):
    """回喂次数用尽（默认 1+2=3 次）→ SplitJSONError，且带最后一次的字段错误。"""
    ctx, state = _ctx_and_state(split_root)
    bad = dict(VALID_SPLIT, next_block={**VALID_SPLIT["next_block"], "id": ""})
    driver, calls = _counting_split_driver([bad])
    events = []
    with pytest.raises(SplitJSONError) as ei:
        call_split_agent(ctx, "m02", collect_split_context(ctx, state, "m02"), driver=driver,
                         on_event=lambda k, d: events.append((k, d)))
    assert calls["n"] == 3
    assert "连续 3 次失败" in str(ei.value)
    assert events[-1][0] == "split.protocol_exhausted"


def test_split_agent_retries_configurable(split_root, monkeypatch):
    ctx, state = _ctx_and_state(split_root)
    bad = dict(VALID_SPLIT, next_block={**VALID_SPLIT["next_block"], "objective": ""})
    driver, calls = _counting_split_driver([bad])
    with pytest.raises(SplitJSONError):
        call_split_agent(ctx, "m02", collect_split_context(ctx, state, "m02"),
                         driver=driver, retries=0)
    assert calls["n"] == 1          # retries=0 → 不回喂，一次定生死


def test_split_agent_infra_failure_not_retried(split_root):
    """缺 fw-spawn.py / dsh 未就绪（exit 2）= 基础设施故障：不回喂，快失败并正确归因。"""
    ctx, state = _ctx_and_state(split_root)
    calls = {"n": 0}

    def fn(actx):
        calls["n"] += 1
        return DriverOutcome(status="error", reason="agent 非零退出(2)",
                             detail={"exit": 2, "stderr": "缺 fw-spawn.py（FW_SPAWN 或候选路径均未命中）"})
    with pytest.raises(SplitInfraError) as ei:
        call_split_agent(ctx, "m02", collect_split_context(ctx, state, "m02"),
                         driver=InlineAgentDriver(fn))
    assert calls["n"] == 1
    assert "基础设施" in str(ei.value)


def test_split_agent_missing_outcome_is_infra(split_root):
    """status=ok 但没有任何拆解产物 = 脚本没落 outcome，属环境问题，不是模型不合规。"""
    ctx, state = _ctx_and_state(split_root)
    calls = {"n": 0}

    def fn(actx):
        calls["n"] += 1
        return DriverOutcome(status="ok", detail={})
    with pytest.raises(SplitInfraError):
        call_split_agent(ctx, "m02", collect_split_context(ctx, state, "m02"),
                         driver=InlineAgentDriver(fn))
    assert calls["n"] == 1


def test_split_agent_parse_meta_reaches_event(split_root):
    """层④ 降级留痕：fw-split.sh 写的 detail._parse 必须透传到事件（不静默降级）。"""
    ctx, state = _ctx_and_state(split_root)
    driver, _calls = _counting_split_driver([VALID_SPLIT],
                                            parse_meta={"layer": 2, "repaired": True,
                                                        "source": "llmjson", "truncated": False})
    events = []
    call_split_agent(ctx, "m02", collect_split_context(ctx, state, "m02"), driver=driver,
                     on_event=lambda k, d: events.append((k, d)))
    meta = events[-1][1]
    assert meta["layer"] == 2 and meta["repaired"] is True and meta["source"] == "llmjson"


def test_split_protocol_retries_from_runtime_config(split_root):
    """split_protocol_retries 是 runtime 键（任务书/dflow.yaml/CLI 三通道都要能覆盖）。"""
    from fw_runner.config import RUNTIME_KEYS
    from fw_runner.context import _resolve_runtime_config
    assert "split_protocol_retries" in RUNTIME_KEYS
    cfg = _resolve_runtime_config({"runtime": {}}, overrides={"split_protocol_retries": "0"})
    assert cfg.split_protocol_retries == 0
    ctx, state = _ctx_and_state(split_root)
    ctx.config.split_protocol_retries = 0
    bad = dict(VALID_SPLIT, next_block={**VALID_SPLIT["next_block"], "id": ""})
    driver, calls = _counting_split_driver([bad])
    with pytest.raises(SplitJSONError):
        call_split_agent(ctx, "m02", collect_split_context(ctx, state, "m02"), driver=driver)
    assert calls["n"] == 1          # 配置生效：不回喂


def test_split_protocol_retries_env_and_negative(split_root, monkeypatch):
    """env 在调用时读取（不是 import 时）；负数按 0 处理，不会变成多次调用。"""
    ctx, state = _ctx_and_state(split_root)
    monkeypatch.setenv("FW_SPLIT_PROTOCOL_RETRIES", "1")
    assert _resolve_protocol_retries(ctx) == 1
    monkeypatch.setenv("FW_SPLIT_PROTOCOL_RETRIES", "-5")
    assert _resolve_protocol_retries(ctx) == 0
    assert _resolve_protocol_retries(ctx, explicit=3) == 3      # 显式参数最高



def test_dynamic_remaining_decay():
    """M1（7a）：剩余量按 done/todo 占比衰减——done 增长单调递减（死循环诱因修复）。"""
    from fw_runner.split import _dynamic_remaining as dr
    assert dr(700, 0, 3) == (700, "review_dynamic")      # 首轮：无完成，剩余=全量
    assert dr(700, 1, 2) == (467, "review_dynamic")      # 完成 1/3 → 衰减
    assert dr(700, 2, 1) == (233, "review_dynamic")      # 完成 2/3 → 继续衰减
    assert dr(700, 3, 0) == (1, "review_dynamic")        # 全完成 → 收尾
    assert dr(None, 1, 2) == (None, "static_fallback")   # 静态缺失 → 回退留痕
    assert dr("abc", 1, 2) == ("abc", "static_fallback")
    assert dr(0, 1, 2) == (0, "static_fallback")
