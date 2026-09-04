"""v1.0 运行主循环（C1–C8）+ F4 三态校验。

- F4   `_valid` 接受三态 verdict（pass/partial/block）
- C1   `_run_module` partial 分支：RETRY 续做 / SPLIT 拆分 / HUMAN 回人
- C2/C3 block 分支消费 UPGRADE_MODEL → `_upgrade_model`（pro 兜底，仅叶子模块）
- C4   `_do_split` 全流程（含拆解 JSON 校验失败 → split_failed → 回人）
- C5   `_aggregate_parents` 父模块聚合 done
- C6   `_check_merge_back` 子模块连续失败合并回父（保留产出）
- C7   每批完成后聚合（端到端 run）
- C8   split 子模块插入 module_order + 依赖图正确继承（端到端 run）

split agent 真 dsh 调用依赖 bin/fw-split.sh（E 轮），本轮以 mock 驱动验证
（unittest.mock.patch runner.call_split_agent），端到端真调用留 E 轮。
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from fw_runner import runner as runner_mod
from fw_runner.budget_hook import null_gate_from_effective
from fw_runner.context import load_task_context
from fw_runner.drivers import InlineAgentDriver
from fw_runner.events import EventLog
from fw_runner.model import DriverOutcome, RunState
from fw_runner.review import read_review
from fw_runner.runner import (
    _aggregate_parents,
    _check_merge_back,
    _do_split,
    _run_module,
    _upgrade_model,
    run,
)
from fw_runner.split import SHARED_CONTEXT_NAME, SplitJSONError

from helpers import build_task  # noqa: E402

VALID_SPLIT = {
    "action": "split",
    "parent_module": "m01",
    "next_block": {
        "id": "m01a",
        "name": "核心算法",
        "objective": "[后端API 目标] m01a 第一步实现核心算法",
        "deliverables": ["算法实现", "算法单测"],
        "files": ["src/algorithm.py"],
    },
    "remaining_after": {
        "scope": "数据预处理；API 接口；单元测试",
        "estimate_lines": 800,
    },
    "dependency_map": {"m01a": []},
    "context_from_parent": "src/algorithm.py 已完成框架；未完成预处理管线",
}


def _mod(id_: str, name: str, deps=None, acceptance=None):
    return {
        "id": id_, "name": name, "layer": 1, "objective": f"{name} 目标",
        "dependencies": deps or [],
        "interfaces": [{"path": f"/api/{id_}/*", "method": ["GET"], "note": f"{id_} 接口"}],
        "acceptance": acceptance or [f"{id_} 验收：按 contract.yaml 产出 src 产物"],
        "boundaries": [f"{id_} 不跨界"],
    }


@pytest.fixture
def runner_root(tmp_path):
    """m01（4 项交付物，待拆）→ m02；便于 split 端到端验证依赖继承。"""
    mods = [
        _mod("m01", "后端API", deps=[],
             acceptance=["核心算法", "数据预处理", "API 接口", "单元测试"]),
        _mod("m02", "下游", deps=["m01"]),
    ]
    return build_task(tmp_path, "验收C-runner", mods,
                      runtime={"max_parallel": 2, "executor_max_rounds": 8,
                               "retry_before_switch": 2, "max_executor_switches": 1,
                               "end_gate": "auto"})


def _ctx_state(root):
    ctx = load_task_context(root)
    state = RunState()
    state.run_id = "test-run"
    for mid in ctx.module_order:
        state.modules[mid] = "pending"
        state.failure_counts[mid] = 0
        state.ensure(mid)
    return ctx, state


def _elog(tmp_path) -> EventLog:
    return EventLog(tmp_path / "dispatch.jsonl", "test-run")


def _events(root) -> list[dict]:
    path = root / "总日志" / "dispatch.jsonl"
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


# ---------- F4：_valid 接受三态 ----------

def test_valid_accepts_partial_verdict(runner_root, tmp_path):
    """F4：auditor 输出 partial 判定不再被判格式非法（不触发 format_invalid 重跑）。"""
    ctx, state = _ctx_state(runner_root)
    events = _elog(tmp_path)

    def audit(ctx_):
        return DriverOutcome(status="ok", verdict="partial", root="self",
                             passed_count=2, total_count=4,
                             remaining_items=["API 接口", "单元测试"],
                             confidence=0.6, reason="部分完成")

    aout = runner_mod._auditor_round(InlineAgentDriver(audit), ctx, state, events,
                                     "m01", 1, "E1", ctx.config)
    assert aout.verdict == "partial"
    assert aout.passed_count == 2
    assert aout.total_count == 4
    names = {e["event"] for e in events.read_all()}
    assert "auditor.format_invalid" not in names
    assert "auditor.format_failed" not in names


# ---------- C1：partial 分支 ----------

def test_partial_high_ratio_retry_then_pass(runner_root, harness):
    """C1 partial→RETRY：完成度高且剩余少 → 同 executor 续做（不拆）。"""
    audits = {"m01": 0}

    def audit_fn(ctx):
        if ctx.module.id == "m01":
            audits["m01"] += 1
            if audits["m01"] == 1:
                return DriverOutcome(status="ok", verdict="partial", root="self",
                                     passed_count=3, total_count=4,
                                     remaining_items=["单元测试"], confidence=0.6,
                                     reason="3/4 完成，剩单元测试")
            return DriverOutcome(status="ok", verdict="pass", evidence_level="L2", root="", confidence=0.9)
        return DriverOutcome(status="ok", verdict="pass", evidence_level="L2", root="", confidence=0.9)

    harness.audit_fn = audit_fn
    result = run(runner_root, executor_driver=harness.make_executor(),
                 auditor_driver=harness.make_auditor())

    assert result.status == "complete", result.to_dict()
    assert result.modules["m01"]["status"] == "done"
    # m01 跑 2 轮 executor（第 1 轮 partial 续做、第 2 轮 pass）
    assert len([c for c in harness.exec_calls if c["mid"] == "m01"]) == 2
    partial_events = [e for e in _events(runner_root) if e["event"] == "module.partial"]
    assert partial_events and partial_events[0]["detail"]["action"] == "retry"
    assert partial_events[0]["detail"]["passed_count"] == 3
    assert partial_events[0]["detail"]["total_count"] == 4
    assert "split" not in {e["event"] for e in _events(runner_root)}


def test_partial_low_ratio_split_ok(runner_root, harness):
    """C1 partial→SPLIT（split_ok）：低完成度说明模块太大 → 拆；父标记 split 不再执行。"""
    def audit_fn(ctx):
        if ctx.module.id == "m01":
            return DriverOutcome(status="ok", verdict="partial", root="self",
                                 passed_count=1, total_count=4,
                                 remaining_items=["数据预处理", "API 接口", "单元测试"],
                                 confidence=0.5, reason="只完成 1/4")
        return DriverOutcome(status="ok", verdict="pass", evidence_level="L2", root="", confidence=0.9)

    harness.audit_fn = audit_fn
    with mock.patch.object(runner_mod, "call_split_agent", return_value=VALID_SPLIT) as m_split:
        result = run(runner_root, executor_driver=harness.make_executor(),
                     auditor_driver=harness.make_auditor())

    assert m_split.called
    assert result.status == "complete", result.to_dict()
    assert result.modules["m01"]["status"] == "done"      # 聚合后 done
    events = _events(runner_root)
    assert any(e["event"] == "module.split" for e in events)
    split_evt = next(e for e in events if e["event"] == "module.split")
    assert split_evt["detail"]["children"] == ["m01a"]
    assert split_evt["detail"]["split_depth"] == 1
    # 子模块已执行并 done
    assert result.modules["m01a"]["status"] == "done"
    # m01 只跑 1 轮 executor（拆分后父不再执行）
    assert len([c for c in harness.exec_calls if c["mid"] == "m01"]) == 1
    assert any(e["event"] == "module.aggregated" for e in events)


def test_partial_split_failed_human(runner_root, harness):
    """C1 partial→SPLIT 但拆解失败（split_failed）→ 回人，不硬拆。"""
    def audit_fn(ctx):
        if ctx.module.id == "m01":
            return DriverOutcome(status="ok", verdict="partial", root="self",
                                 passed_count=1, total_count=4,
                                 remaining_items=["b", "c", "d"], confidence=0.5)
        return DriverOutcome(status="ok", verdict="pass", evidence_level="L2", root="", confidence=0.9)

    harness.audit_fn = audit_fn
    with mock.patch.object(runner_mod, "call_split_agent",
                           side_effect=SplitJSONError("cannot_split: 已是叶子模块")):
        result = run(runner_root, executor_driver=harness.make_executor(),
                     auditor_driver=harness.make_auditor())

    assert result.status == "needs_human", result.to_dict()
    assert result.needs_human == ["m01"]
    events = _events(runner_root)
    assert any(e["event"] == "module.split_failed" for e in events)
    # 父模块未被标记 split（拆分失败不硬拆）
    assert result.modules["m01"]["status"] == "needs_human"


def test_partial_human_when_split_capacity_exhausted(runner_root, tmp_path):
    """C1 partial→HUMAN：拆分深度到上限（不能再拆）→ 回人。

    说明：split 配置的 runtime 接线（context.py）属后续轮次，本轮直接注入
    RunConfig/state 做单元级验证（C1 分支行为）。
    """
    ctx, state = _ctx_state(runner_root)
    events = _elog(tmp_path)
    cfg = ctx.config
    state.ensure("m01").split_depth = cfg.split_max_depth   # 拆分深度已到上限

    def audit_fn(ctx_):
        return DriverOutcome(status="ok", verdict="partial", root="self",
                             passed_count=1, total_count=4,
                             remaining_items=["b", "c", "d"], confidence=0.5)

    budget = null_gate_from_effective(ctx.effective.get("budget"))
    outcome = _run_module(ctx, state, "m01",
                          InlineAgentDriver(lambda c: DriverOutcome(status="ok", substance=True)),
                          InlineAgentDriver(audit_fn), cfg, budget, events)

    assert outcome == "human"
    assert state.modules["m01"] == "needs_human"
    partial_events = [e for e in events.read_all() if e["event"] == "module.partial"]
    assert partial_events and partial_events[0]["detail"]["action"] == "human"


# ---------- C2/C3：UPGRADE_MODEL / _upgrade_model ----------

def test_upgrade_model_side_effects(runner_root, tmp_path):
    """C3：_upgrade_model model_tier+1、block_count 清零、executor_id 换 E{n}_pro、emit 事件。"""
    ctx, state = _ctx_state(runner_root)
    events = _elog(tmp_path)
    astate = state.ensure("m01")
    astate.executor_switches = 1
    astate.block_count = 5
    astate.model_tier = 0
    astate.executor_id = "E2"

    _upgrade_model(ctx, state, "m01", events)

    assert astate.model_tier == 1
    assert astate.block_count == 0
    assert astate.executor_id == "E2_pro"
    evt = [e for e in events.read_all() if e["event"] == "module.model_upgrade"]
    assert len(evt) == 1
    assert evt[0]["detail"]["model_tier"] == 1
    assert evt[0]["detail"]["model"] == "pro"          # ctx.config.model_tiers[1]


def test_block_upgrade_model_pro_rerun_then_human(runner_root, tmp_path):
    """C2/C3：block 分支消费 UPGRADE_MODEL → _upgrade_model → 用 pro 重跑当前模块。

    enable_split=False（直接注入 RunConfig）跳过 SPLIT 直达 UPGRADE_MODEL；
    pro 兜底后仍 block → 回人。executor 序列 E1×2 → E2×2 → E2_pro×2。
    """
    ctx, state = _ctx_state(runner_root)
    events = _elog(tmp_path)
    cfg = ctx.config
    cfg.enable_split = False
    exec_ids: list[str] = []

    def exec_fn(ctx_):
        from fw_runner.review import append_done
        exec_ids.append(ctx_.executor_id)
        append_done(ctx_.module.review_path, f"exec {ctx_.round_no} ({ctx_.executor_id})")
        return DriverOutcome(status="ok", substance=True, tokens=0)

    def audit_fn(ctx_):
        return DriverOutcome(status="ok", verdict="block", root="self", confidence=0.4,
                             reason="持续 block", blocker="b")

    budget = null_gate_from_effective(ctx.effective.get("budget"))
    outcome = _run_module(ctx, state, "m01", InlineAgentDriver(exec_fn),
                          InlineAgentDriver(audit_fn), cfg, budget, events)

    assert outcome == "human"
    assert state.modules["m01"] == "needs_human"
    astate = state.ensure("m01")
    assert astate.executor_id == "E2_pro"          # 最后停在 pro executor
    assert astate.model_tier == 1                  # 0→1（flash→pro）
    assert astate.block_count == 2                 # pro 兜底后仍被 block 2 次
    upgrades = [e for e in events.read_all() if e["event"] == "module.model_upgrade"]
    assert len(upgrades) == 1
    assert upgrades[0]["detail"]["model"] == "pro"
    assert exec_ids == ["E1", "E1", "E2", "E2", "E2_pro", "E2_pro"], exec_ids


# ---------- C4：_do_split ----------

def test_do_split_full_flow(runner_root, tmp_path):
    """C4：_do_split 收集上下文 → 调 split agent → scaffold → SHARED_CONTEXT → 父 split。"""
    ctx, state = _ctx_state(runner_root)
    events = _elog(tmp_path)
    aout = DriverOutcome(status="ok", verdict="partial", passed_count=1, total_count=4,
                         remaining_items=["b", "c", "d"])

    with mock.patch.object(runner_mod, "call_split_agent", return_value=VALID_SPLIT) as m_split:
        result = _do_split(ctx, state, "m01", events, aout)

    assert result == "split_ok"
    assert m_split.called
    astate = state.ensure("m01")
    assert state.modules["m01"] == "split"
    assert astate.split_depth == 1
    assert astate.child_modules == ["m01a"]
    # 子模块入 order（父之后）且依赖继承
    assert ctx.module_order == ["m01", "m01a", "m02"]
    assert ctx.dependencies["m01a"] == []
    # SHARED_CONTEXT.md 生成在父目录
    assert (ctx.modules["m01"].dir / SHARED_CONTEXT_NAME).is_file()
    # 子模块状态 pending + parent 指向
    assert state.modules["m01a"] == "pending"
    assert state.ensure("m01a").parent_module == "m01"
    evt = [e for e in events.read_all() if e["event"] == "module.split"]
    assert evt and evt[0]["detail"]["children"] == ["m01a"]


def test_do_split_json_invalid_returns_split_failed(runner_root, tmp_path):
    """C4 校验失败路径：拆解 JSON 非法 → split_failed 事件，父不标记 split。"""
    ctx, state = _ctx_state(runner_root)
    events = _elog(tmp_path)
    bad = {"action": "split", "new_modules": []}   # 缺 parent_module/dependency_map/context

    with mock.patch.object(runner_mod, "call_split_agent",
                           side_effect=SplitJSONError("action 必须是 'split'")):
        result = _do_split(ctx, state, "m01", events, None)

    assert result == "split_failed"
    assert state.modules["m01"] != "split"         # 不硬拆
    evt = [e for e in events.read_all() if e["event"] == "module.split_failed"]
    assert len(evt) == 1
    assert "SplitJSONError" in evt[0]["detail"]["error"]


# ---------- C5：_aggregate_parents ----------

def test_aggregate_parents_all_children_done(runner_root, tmp_path):
    """C5：父 split 且子模块全 done → 父聚合 done + module.aggregated。"""
    ctx, state = _ctx_state(runner_root)
    events = _elog(tmp_path)
    astate = state.ensure("m01")
    astate.child_modules = ["m01a", "m01b"]
    astate.split_depth = 1
    state.modules["m01"] = "split"
    state.modules["m01a"] = "done"
    state.modules["m01b"] = "done"

    changed = _aggregate_parents(ctx, state, events)

    assert changed is True
    assert state.modules["m01"] == "done"
    assert astate.aggregated is True
    assert "m01" in state.completed_order
    evt = [e for e in events.read_all() if e["event"] == "module.aggregated"]
    assert evt and evt[0]["detail"]["children"] == ["m01a", "m01b"]


def test_aggregate_parents_waits_for_all_children(runner_root, tmp_path):
    """C5：任一子模块未 done → 父不聚合。"""
    ctx, state = _ctx_state(runner_root)
    events = _elog(tmp_path)
    astate = state.ensure("m01")
    astate.child_modules = ["m01a", "m01b"]
    state.modules["m01"] = "split"
    state.modules["m01a"] = "done"
    state.modules["m01b"] = "running"

    assert _aggregate_parents(ctx, state, events) is False
    assert state.modules["m01"] == "split"


# ---------- C6：_check_merge_back ----------

def _split_state_with_children(runner_root):
    import tempfile
    ctx, state = _ctx_state(runner_root)
    elog_path = Path(tempfile.mkdtemp()) / "dispatch.jsonl"
    with mock.patch.object(runner_mod, "call_split_agent", return_value=VALID_SPLIT):
        _do_split(ctx, state, "m01", EventLog(elog_path, "x"), None)
    return ctx, state


def test_merge_back_preserves_artifacts(runner_root, tmp_path):
    """C6：子模块连续失败达阈值 → 合并回父，文件移回父 src + REVIEW merge。"""
    ctx, state = _split_state_with_children(runner_root)
    events = _elog(tmp_path)
    # 子模块产出 + 已做
    m01a_src = ctx.modules["m01a"].dir / "src" / "algorithm.py"
    m01a_src.write_text("core\n", encoding="utf-8")
    from fw_runner.review import append_done
    append_done(ctx.modules["m01a"].review_path, "核心算法框架完成")
    # 触发子模块连续失败
    state.ensure("m01a").parent_module = "m01"
    state.ensure("m01a").partial_count = 3            # split_merge_after_fails 默认 3

    result = _check_merge_back(ctx, state, "m01a", events)

    assert result is True
    # 文件移到父 src/
    assert (ctx.modules["m01"].dir / "src" / "algorithm.py").is_file()
    assert not m01a_src.exists()
    # 子模块状态删除，父恢复 pending、child_modules 清空
    assert "m01a" not in state.modules
    assert "m01a" not in state.per_module
    assert state.modules["m01"] == "pending"
    assert state.ensure("m01").child_modules == []
    # 子模块从 ctx 移除（防被重新调度 / 下次拆分 id 冲突）
    assert "m01a" not in ctx.modules
    assert "m01a" not in ctx.module_order
    assert ctx.module_order == ["m01", "m02"]
    # REVIEW merge：父 REVIEW 已做含子模块条目
    doc = read_review(ctx.modules["m01"].review_path)
    assert any("核心算法框架完成" in ln for ln in doc.list_done())
    # module.merge_back 事件
    evt = [e for e in events.read_all() if e["event"] == "module.merge_back"]
    assert evt and evt[0]["detail"]["failed_child"] == "m01a"


def test_merge_back_no_parent_or_below_threshold(runner_root, tmp_path):
    """C6：无父模块 / 未达阈值 → 不合并。"""
    ctx, state = _ctx_state(runner_root)
    events = _elog(tmp_path)
    # 顶层模块（无 parent）→ False
    assert _check_merge_back(ctx, state, "m01", events) is False
    # 子模块但 partial_count 未达阈值 → False
    ctx2, state2 = _split_state_with_children(runner_root)
    state2.ensure("m01a").parent_module = "m01"
    state2.ensure("m01a").partial_count = 2
    assert _check_merge_back(ctx2, state2, "m01a", events) is False
    assert state2.modules["m01"] == "split"           # 未触发合并


# ---------- C7/C8：端到端 拆分 → 子模块执行 → 聚合 → 依赖继承 ----------

def test_end_to_end_split_aggregate_and_dependency(runner_root, harness):
    """C7/C8：m01 拆分 → 子模块按依赖执行 → 每批聚合 → m02 等 m01 聚合 done 后才跑。"""
    def audit_fn(ctx):
        if ctx.module.id == "m01":
            return DriverOutcome(status="ok", verdict="partial", root="self",
                                 passed_count=1, total_count=4,
                                 remaining_items=["数据预处理", "API 接口", "单元测试"],
                                 confidence=0.5, reason="只完成 1/4")
        return DriverOutcome(status="ok", verdict="pass", evidence_level="L2", root="", confidence=0.9)

    harness.audit_fn = audit_fn
    with mock.patch.object(runner_mod, "call_split_agent", return_value=VALID_SPLIT):
        result = run(runner_root, executor_driver=harness.make_executor(),
                     auditor_driver=harness.make_auditor())

    assert result.status == "complete", result.to_dict()
    assert set(result.completed) == {"m01", "m01a", "m02"}
    assert result.modules["m01"]["status"] == "done"
    assert result.modules["m01a"]["status"] == "done"
    assert result.modules["m02"]["status"] == "done"
    # 事件序列：split → 子模块 done → aggregated → m02 done
    events = _events(runner_root)
    kinds = [e["event"] for e in events]
    assert "module.split" in kinds
    assert "module.aggregated" in kinds
    # 聚合先于 m02 完成（m02 依赖 m01 聚合 done）
    agg_seq = next(i for i, e in enumerate(events) if e["event"] == "module.aggregated")
    m02_done_seq = next(i for i, e in enumerate(events)
                        if e["event"] == "module.done" and e["module"] == "m02")
    assert agg_seq < m02_done_seq, "m02 应在 m01 聚合 done 之后完成"
    # completed_order 以"真正完成"为序：子模块 done → 父聚合 done → m02（依赖 m01 聚合）
    assert result.completed == ["m01a", "m01", "m02"], result.completed
    # 每批聚合（C7）：aggregated 事件恰一次
    assert len([e for e in events if e["event"] == "module.aggregated"]) == 1


# ---------- 2026-09-04 小澈复查：升级链计数口径 + 任务级失控闸 ----------

def test_switch_executor_resets_partial_failure_budget(runner_root, tmp_path):
    """换 executor 必须清掉"同因零进展"额度：新 executor 不该背着前任的失败次数。"""
    from fw_runner.upgrade import switch_executor
    ctx, state = _ctx_state(runner_root)
    events = _elog(tmp_path)
    a = state.ensure("m01")
    a.no_progress_streak = 2
    a.last_remaining_sig = "还差登录接口"
    a.executor_id = "E1"
    switch_executor(ctx, state, "m01", events, reason="测试换人")
    assert a.executor_switches == 1
    assert a.no_progress_streak == 0, "换人未清零 → 新 executor 一上场就到回人线"
    assert a.last_remaining_sig == ""


def test_split_max_total_blocks_runaway_without_calling_agent(runner_root, tmp_path):
    """任务级模块总数闸：到顶就不再拆，而且**不白花一次 split agent 调用**。"""
    ctx, state = _ctx_state(runner_root)
    events = _elog(tmp_path)
    ctx.config.split_max_total = len(ctx.modules)      # 故意设成已用满
    with mock.patch.object(runner_mod, "call_split_agent") as spy:
        got = _do_split(ctx, state, "m01", events, None)
    assert got == "split_failed"
    spy.assert_not_called()
    lines = (tmp_path / "dispatch.jsonl").read_text(encoding="utf-8").splitlines()
    errs = [json.loads(x).get("detail", {}).get("error", "") for x in lines]
    assert any("防失控递归" in e for e in errs), errs
