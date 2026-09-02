"""执行编排主循环（需求4）：依赖图拓扑 → 并行调度 → 升级链 → checkpoint/resume。

流程（对每个批次）：批内模块并行（≤ max_parallel）→ 每模块独立走
  executor 轮 → auditor 判定（三态 pass/partial/block）→ 升级链
  （retry → switch → SPLIT → UPGRADE_MODEL → human）→ 心跳守护；
批次间串行（下游等上游**完成**），每批后重算批次（split 子模块动态入队）并聚合已拆父模块。
预算闸门与集成验收为对接钩子（见 budget_hook / integrate_hook，主体分别归需求5/需求6 轮）。

checkpoint：每 checkpoint_every 模块完成 + 关键状态转移（回人/预算停/中断/结束）都原子写
总日志/快照.json；--resume-from-checkpoint 读快照 → 已完成模块不重跑（executor/auditor
不再被调用），计数从快照续接。
"""
from __future__ import annotations

import shutil
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from .checkpoint import (
    read_snapshot,
    snapshot_to_state,
    write_checkpoint,
)
from .context import RunnerInputError, TaskContext, load_task_context
from .drivers import AgentContext, AgentDriver, InlineAgentDriver, ScriptedAgentDriver
from .events import EventLog, existing_run_ids, new_run_id
from .fork import run_parallel
from .heartbeat import detect_stall, should_escalate
from .integrate_hook import IntegrationHook, NullIntegrationHook, append_integration_log
from .model import DriverOutcome, ModuleSpec, RunConfig, RunnerResult, RunState, now_iso
from .progress import ensure_progress
from .registry import complete_run, register_run
from .review import append_done, fingerprint, read_review, set_values, write_handover
from .scheduler import plan_batches
from .upgrade import (
    HUMAN, RETRY, SWITCH, SPLIT, UPGRADE_MODEL, DONE, RETRY_BACKOFF,
    assign_initial_executor, route_partial, route_stuck, route_verdict,
    should_merge_back, switch_executor, sync_review,
)
from .human import apply_human_answers, human_escalate
from .split import (
    CannotSplitError, build_wrapup_split_json, call_split_agent,
    collect_split_context, generate_shared_context, insert_children_into_order,
    scaffold_children,
)
from .budget_hook import BudgetGate, null_gate_from_effective

_EXIT_HUMAN = 2
_EXIT_INTERRUPTED = 130


def _book_remaining_estimate(book_path: Path):
    """任务书 modules[0].remaining_estimate.estimate_lines（分块剩余量，planner 拆模块时定的权威值）。

    出口判定用：auditor 判 pass 后，读任务书的 remaining_estimate 决定 split / final / done。
    不依赖 executor 自报 remaining_lines —— remaining 不是 executor 的活（它只做 first_block），
    是 planner 拆模块时定死的量，程序直接从任务书读（2026-08-25 重构：消灭"executor 未自报→静默生吞"）。
    读取失败/结构异常 → None（视为无剩余，主流程不中断）。
    """
    try:
        import yaml  # noqa: PLC0415 — runner 主流程不依赖 yaml，仅此出口判定路径用
        doc = yaml.safe_load(Path(book_path).read_text(encoding="utf-8")) or {}
        mods = doc.get("modules")
        mod = mods[0] if isinstance(mods, list) and mods else {}
        rem = mod.get("remaining_estimate") or {}
        val = rem.get("estimate_lines")
        return int(val) if val is not None else None
    except Exception:
        return None


def _book_remaining(book_path) -> dict:
    """读任务书 remaining_estimate 完整 dict（scope + estimate_lines，planner 定的权威剩余）。

    final_block 收官轮注入用：runner 把剩余内容传给 executor/auditor 作为本轮必做/必验目标
    （2026-08-27 修复"final 续做轮拿到 first_block 指令 → 剩余被吞"）。
    """
    try:
        import yaml  # noqa: PLC0415
        doc = yaml.safe_load(Path(book_path).read_text(encoding="utf-8")) or {}
        mods = doc.get("modules")
        mod = mods[0] if isinstance(mods, list) and mods else {}
        return dict(mod.get("remaining_estimate") or {})
    except Exception:
        return {}


def _inject_final_block(actx: AgentContext, module_spec) -> None:
    """final_block 收官轮：把 remaining 注入 AgentContext env（executor/auditor 共用）。

    本轮 executor 的任务 = remaining_estimate 描述的剩余部分；auditor 的验收 = 剩余做全了没。
    """
    rem = _book_remaining(module_spec.book_path)
    actx.env["FW_FINAL_BLOCK"] = "1"
    if rem.get("scope"):
        actx.env["FW_REMAINING_SCOPE"] = str(rem["scope"])
    if rem.get("estimate_lines") is not None:
        actx.env["FW_REMAINING_LINES"] = str(rem["estimate_lines"])


def _module_final_round(state, mid: str) -> bool:
    """安全读模块是否处于 final_block 收官轮（测试 mock 的 state 无 ensure 时返回 False）。"""
    try:
        astate = state.ensure(mid)
        return bool(getattr(astate, "final_round", False))
    except Exception:
        return False


class RunInterrupted(Exception):
    """运行被中断（driver 报告 interrupted / 外部 SIGINT）。已写 checkpoint，可 resume。"""


@dataclass
class _BatchResult:
    module_id: str
    outcome: str        # done | human


def _module_dicts(ctx: TaskContext) -> List[Dict[str, Any]]:
    return [ctx.modules[mid].to_dict() for mid in ctx.module_order]


def _validate_module_dir_shape(ctx: TaskContext) -> None:
    """脚手架结构前置校验：REVIEW.md/contract.yaml/任务书-*/交付说明.md 存在。"""
    errs: List[str] = []
    for mid in ctx.module_order:
        spec = ctx.modules[mid]
        for label, p in (
            ("REVIEW.md", spec.review_path), ("contract.yaml", spec.contract_path),
            ("任务书", spec.book_path), ("交付说明.md", spec.delivery_path),
        ):
            if not p.is_file():
                errs.append(f"{mid}: 缺 {label} ({p})")
    if errs:
        raise RunnerInputError("任务根结构不完整（应先 fw-scaffold 生成）:\n" + "\n".join(f"  - {e}" for e in errs))


def _rotate_stale_dispatch(ctx: TaskContext) -> None:
    """事件 seq 完整性（dsh 事件流能力）：对同一任务根从零重新 run 时，
    若 dispatch.jsonl 已含其他 run_id 的事件流，先把旧文件归档为
    总日志/dispatch-archive-<时间戳>.jsonl（保留审计轨迹），再让新 run 从干净 seq=1 开始。

    resume 路径不旋转（延续同一 run_id 链）；显式注入 event_log 的调用方自行负责。
    """
    path = ctx.dispatch_path()
    old_ids = existing_run_ids(path)
    if not old_ids:
        return
    import datetime as _dt
    archive = path.with_name(f"dispatch-archive-{_dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.jsonl")
    try:
        import os as _os
        _os.replace(str(path), str(archive))
    except OSError:
        return


def run(task_root: str | Path,
        *,
        overrides: Optional[Mapping[str, Any]] = None,
        mode: str = "speed_first",
        resume: bool = False,
        executor_driver: Optional[AgentDriver] = None,
        auditor_driver: Optional[AgentDriver] = None,
        budget_gate: Optional[BudgetGate] = None,
        integration_hook: Optional[IntegrationHook] = None,
        run_id: Optional[str] = None,
        event_log: Optional[EventLog] = None,
        ) -> RunnerResult:
    """执行一次任务编排。返回 RunnerResult（status/exit_reason/checkpoint/模块明细…）。

    中断：RunInterrupted 上抛（CLI 层转 130 退出）；快照已写，--resume-from-checkpoint 续跑。
    """
    t0 = time.monotonic()
    ctx = load_task_context(task_root, overrides=overrides, mode=mode)
    _validate_module_dir_shape(ctx)
    cfg = ctx.config
    budget = budget_gate if budget_gate is not None else null_gate_from_effective(ctx.effective.get("budget"))
    hook = integration_hook if integration_hook is not None else NullIntegrationHook()

    # ---- 事件流 + 状态 ----
    if event_log is None:
        if not resume:
            _rotate_stale_dispatch(ctx)   # 同一任务根重复从零 run：归档旧 run_id 事件流
        event_log = EventLog(ctx.dispatch_path(), run_id or new_run_id())
    state = RunState()
    state.run_id = event_log.run_id
    for mid in ctx.module_order:
        state.modules[mid] = "pending"
        state.failure_counts[mid] = 0

    if resume:
        snap = read_snapshot(ctx.task_root)
        if snap is None:
            raise RunnerInputError("--resume-from-checkpoint 但找不到总日志/快照.json（从未运行过？）")
        state = snapshot_to_state(ctx, snap)
        if not state.run_id:
            state.run_id = event_log.run_id
        event_log = EventLog(ctx.dispatch_path(), state.run_id, start_seq=state.last_seq)
        event_log.emit("run.resume", detail={
            "snapshot_status": str(snap.get("status") or ""),
            "completed": list(state.completed_order),
            "needs_human": list(state.needs_human),
            "last_seq": state.last_seq,
        })
        # v1.0 H2：读 human_answer.json，按真人回复收敛 needs_human 模块
        # （[A]放弃→跳过 / [B]改方案·[D]自定义→重新执行 / [C]暂停→任务保持暂停）
        _human_paused = (apply_human_answers(ctx, state, event_log) == "paused")
    else:
        event_log.emit("run.start", detail={
            "task": ctx.task_name, "mode": cfg.mode, "config": cfg.to_dict(),
            "modules": _module_dicts(ctx),
        })
        _human_paused = False
        state.last_seq = event_log.last_seq
        write_checkpoint(ctx, state, "running", "run.start", note="初始 checkpoint")

    events_all: List[Dict[str, Any]] = []

    # ---- 注册表登记（dashboard 数据桥可见）：run_id 已最终确定（resume 沿用快照 run_id）----
    # 幂等：新 run 首登 active（启动即被 /api/runs 聚合、面板自动跟随）；
    # resume 续跑沿用同一 run_id 时已存在则跳过（不重复插入、不覆盖），run 实时状态由
    # 数据桥从快照 stage 派生。失败仅告警，绝不阻塞开跑。
    register_run(state.run_id, str(ctx.task_root), ctx.task_name)

    def _checkpoint(status: str, cause: str, note: str = ""):
        state.last_seq = event_log.last_seq
        return write_checkpoint(ctx, state, status, cause, note)

    try:
        if _human_paused:
            _checkpoint("needs_human", "human_paused", note="真人选择 [C]暂停任务；继续处理后再 --resume-from-checkpoint")
            return _result(ctx, state, event_log, budget, hook, budget.check(), t0,
                           status="needs_human", exit_reason="human_paused", exit_code=_EXIT_HUMAN,
                           payload={"human_paused": True})
        # 动态分批主循环（v1.0 C7/C8）：每批后重算批次 —— split 出的子模块插入
        # module_order 后可在下一批被调度；每批结束调 _aggregate_parents 聚合父模块。
        while True:
            done_ids = ({mid for mid, s in state.modules.items() if s == "done"}
                        | set(state.needs_human))
            # split 父模块（容器）不再执行，也不视为 done（其下游仍等它聚合 done）
            pending = [mid for mid in ctx.module_order
                       if mid not in done_ids and state.modules.get(mid) != "split"]
            if not pending:
                break
            batches = plan_batches([ctx.modules[mid].to_dict() for mid in ctx.module_order],
                                   cfg.max_parallel, completed=done_ids)
            batch: List[str] = []
            for b in batches:
                ready = [mid for mid in b if mid in pending]
                if ready:
                    batch = ready
                    break
            if not batch:
                break   # 剩余全部为 split 容器 / needs_human，无法推进
            workers = [lambda mid=mid: _run_module(ctx, state, mid, executor_driver,
                                                   auditor_driver, cfg, budget, event_log)
                       for mid in batch]
            results = run_parallel(workers, max_concurrency=cfg.max_parallel)
            for mid, outcome in zip(batch, results):
                if outcome == HUMAN:
                    _checkpoint("needs_human", "escalated_to_human", note=f"模块 {mid} 回人")
                    continue
                # 完成计数 → checkpoint_every 快照
                n_done = len(state.completed_order)
                if n_done and n_done % cfg.checkpoint_every == 0:
                    _checkpoint("running", "checkpoint_every", note=f"已 {n_done} 模块完成")
            # v1.0 C7：每批完成后聚合已拆父模块（子模块全 done → 父 done）
            _aggregate_parents(ctx, state, event_log)
            # 预算闸门（每批后）
            bstatus = budget.check()
            if bstatus.stop:
                event_log.emit("budget.stop", detail={"budget": bstatus.to_dict(),
                                                      "ranking": budget.ranking()})
                _checkpoint("stopped", "budget_stop", note=bstatus.message + "；加预算后 --resume-from-checkpoint 续跑")
                return _result(ctx, state, event_log, budget, hook, bstatus, t0,
                               status="stopped", exit_reason="budget_stop", exit_code=_EXIT_HUMAN,
                               payload={"budget": bstatus.to_dict()})
            if bstatus.warned:
                event_log.emit("budget.warn", detail={"budget": bstatus.to_dict(),
                                                      "ranking": budget.ranking()})

        # ---- 全部批次结束：收尾 ----
        if state.needs_human:
            _checkpoint("needs_human", "escalated_to_human", note="存在回人模块；信息见 needs_human/模块明细")
            return _result(ctx, state, event_log, budget, hook, bstatus := budget.check(), t0,
                           status="needs_human", exit_reason="escalated_to_human", exit_code=_EXIT_HUMAN)

        # 集成验收钩子（需求6 主体）
        report = hook.run(ctx, state)
        seq = event_log.emit("integration.check", detail=report.to_dict())
        append_integration_log(ctx, state.run_id, seq, report, cfg.end_gate)
        state.integration = report.to_dict()
        if report.status == "failed":
            _checkpoint("integration_failed", "integration_failed", note="; ".join(report.notes))
            return _result(ctx, state, event_log, budget, hook, report, t0,
                           status="integration_failed", exit_reason="integration_failed",
                           exit_code=_EXIT_HUMAN, payload={"integration": report.to_dict()})
        if cfg.end_gate == "always":
            _checkpoint("needs_confirmation", "end_gate_always", note="end_gate=always：全部模块完成，等待人工确认")
            return _result(ctx, state, event_log, budget, hook, report, t0,
                           status="needs_confirmation", exit_reason="end_gate_always",
                           exit_code=_EXIT_HUMAN, payload={"integration": report.to_dict()})

        _checkpoint("complete", "all_modules_done", note="全部模块完成 + 集成钩子通过/延迟")
        return _result(ctx, state, event_log, budget, hook, report, t0,
                       status="complete", exit_reason="all_modules_done", exit_code=0,
                       payload={"integration": report.to_dict()})
    except RunInterrupted as e:
        _checkpoint("interrupted", "interrupted", note=f"运行被中断：{e}；--resume-from-checkpoint 续跑，已完成模块不重跑")
        return _result(ctx, state, event_log, budget, hook, None, t0,
                       status="interrupted", exit_reason="interrupted", exit_code=_EXIT_INTERRUPTED,
                       payload={"interrupt": str(e)})


def _run_module(ctx: TaskContext, state: RunState, mid: str,
                executor_driver: Optional[AgentDriver], auditor_driver: Optional[AgentDriver],
                cfg: RunConfig, budget: BudgetGate, events: EventLog) -> str:
    """单模块完整生命周期（executor 轮 → auditor → 升级链 → 心跳）。返回 done|human。"""
    spec = ctx.modules[mid]
    astate = state.ensure(mid)
    if not astate.started_at:
        astate.started_at = now_iso()
    state.modules[mid] = "running"
    assign_initial_executor(state, mid)
    events.emit("module.dispatch", module=mid, detail={
        "executor_id": astate.executor_id, "executor_round": astate.executor_round + 1,
    })

    while state.modules[mid] not in ("done", "needs_human"):
        # 0) v1.0 C6：子模块连续 partial 达阈值 → 合并回父（拆分方向错误兜底）
        if _check_merge_back(ctx, state, mid, events):
            return DONE   # 子模块已合并回父（状态删除，父模块恢复 pending 重新执行）
        # 1) executor 轮数上限 → 卡循环 → switch/human（不给 retry）
        if astate.executor_round >= cfg.executor_max_rounds:
            events.emit("module.stuck", module=mid, detail={
                "reason": "executor_max_rounds", "executor_round": astate.executor_round})
            action = route_stuck(state, mid, cfg, f"executor_max_rounds={cfg.executor_max_rounds}")
            if action == HUMAN:
                return human_escalate(ctx, state, mid, events, root="self",
                                      reason=f"executor 轮数超限({cfg.executor_max_rounds})")
            switch_executor(ctx, state, mid, events, reason="executor_max_rounds")
            continue

        # 2) executor 干活轮
        astate.executor_round += 1
        round_no = astate.executor_round
        exec_id = astate.executor_id
        events.emit("executor.round.start", module=mid, detail={
            "round": round_no, "executor_id": exec_id,
        })
        before_fp = fingerprint(spec)
        outcome = _executor_round(executor_driver, ctx, state, mid, round_no, exec_id, cfg)
        if outcome.status == "interrupted":
            # 中断也算"未落档"风险：兜底进度后再上抛（resume 续跑时新 executor 能看到前任做到哪）
            ensure_progress(spec.delivery_path, executor_id=exec_id, round_no=round_no)
            raise RunInterrupted(f"模块 {mid} 第 {round_no} 轮 executor 被中断")
        after_fp = fingerprint(spec)
        substance = outcome.substance if outcome.substance is not None else (before_fp != after_fp)
        astate.stall_count = 0 if substance else astate.stall_count + 1
        budget.record(mid, outcome.tokens)
        state.budget_used_tokens += outcome.tokens
        astate.tokens_used += outcome.tokens
        events.emit("executor.round.done", module=mid, detail={
            "round": round_no, "executor_id": exec_id,
            "substance": substance, "stall_count": astate.stall_count, "tokens": outcome.tokens,
            "outcome_status": outcome.status,
        })
        sync_review(spec, astate, status="working" if substance else "needs_review")

        # 2.4) 可靠性补丁（功能A）：轮数将尽（剩余 ≤1 轮）或本轮失败/超时 → 兜底落档进度。
        # executor 每轮按 persona 铁律自行写「交付说明.md 进度快照」；此处防未落档导致的
        # 半途而废（换人/回人后只能从头重做）。已有快照则幂等跳过（不覆盖 executor 的落档）。
        if (cfg.executor_max_rounds - round_no) <= 1 or outcome.status == "error":
            if ensure_progress(spec.delivery_path, executor_id=exec_id, round_no=round_no):
                events.emit("executor.progress.archived", module=mid, detail={
                    "round": round_no, "executor_id": exec_id,
                    "reason": ("agent_error" if outcome.status == "error"
                               else f"rounds_near_limit({round_no}/{cfg.executor_max_rounds})"),
                    "delivery": str(spec.delivery_path),
                    "note": "executor 未落档进度，runner 兜底写入占位",
                })

        # 2.5) agent 崩溃/超时 → 进升级链（root 用驱动分类：env upstream=限流/断网/5xx）
        if outcome.status == "error":
            root = outcome.root if outcome.root in ("self", "upstream", "contract") else "self"
            events.emit("executor.round.error", module=mid, detail={
                "round": round_no, "executor_id": exec_id, "reason": outcome.reason,
                "root": root})
            if root == "upstream":
                # 客观环境错误（限流/断网/5xx）：不退避换人，只限次退避重试当前回合
                action = route_stuck(state, mid, cfg, outcome.reason or "upstream环境错误",
                                     root="upstream")
                sync_review(spec, astate, status="blocked", root="upstream",
                            reason=outcome.reason or "upstream环境错误")
                events.emit("module.blocked", module=mid, detail={
                    "action": action, "root": "upstream", "reason": outcome.reason,
                    "env_backoffs": getattr(astate, "env_backoffs", 0),
                    "executor_id": exec_id,
                })
                if action == RETRY_BACKOFF:
                    import time as _t
                    delay = _backoff_delay(getattr(astate, "env_backoffs", 1))
                    events.emit("module.env_backoff", module=mid, detail={
                        "backoff_s": delay, "attempt": getattr(astate, "env_backoffs", 1),
                    })
                    _t.sleep(delay)      # 退避等限流/断网恢复，同一 executor 原样重试
                    continue
                if action == HUMAN:
                    return human_escalate(ctx, state, mid, events, root="upstream",
                                          reason=outcome.reason or "upstream环境错误(退避耗尽)")
                continue
            action = route_verdict(state, mid, cfg, root="self",
                                   reason=outcome.reason or "agent_error")
            sync_review(spec, astate, status="blocked", root="self",
                        reason=outcome.reason or "agent_error")
            events.emit("module.blocked", module=mid, detail={
                "action": action, "root": "self", "reason": outcome.reason,
                "block_count": astate.block_count, "block_total": astate.block_total,
                "executor_switches": astate.executor_switches,
            })
            if action == HUMAN:
                return human_escalate(ctx, state, mid, events, root="self",
                                      reason=outcome.reason or "agent_error")
            if action == SWITCH:
                switch_executor(ctx, state, mid, events, reason=outcome.reason or "agent_error")
            if action == SPLIT:
                result = _do_split(ctx, state, mid, events, None)
                if result == "split_ok":
                    return DONE
                astate.reason = (outcome.reason or "agent_error") + "：模块无法拆分"
                return human_escalate(ctx, state, mid, events, root="self", reason=astate.reason)
            if action == UPGRADE_MODEL:
                _upgrade_model(ctx, state, mid, events)
            continue  # retry / pro 兜底 → 同一 executor 下一轮

        # 3) 心跳守护：连续 N 轮无实质产出 → 判静默卡死 → 进升级链（等效 block/self）
        if should_escalate(astate.stall_count, cfg.heartbeat_n_rounds):
            events.emit("heartbeat.stall", module=mid, detail={
                "stall_count": astate.stall_count, "n": cfg.heartbeat_n_rounds, "round": round_no,
            })
            action = route_verdict(state, mid, cfg, root="stall",
                                   reason=f"心跳守护：连续{cfg.heartbeat_n_rounds}轮无实质产出")
            sync_review(spec, astate, status="blocked", root="stall",
                        reason=f"心跳守护：连续{cfg.heartbeat_n_rounds}轮无实质产出")
            events.emit("module.blocked", module=mid, detail={
                "action": action, "root": "stall", "block_count": astate.block_count,
                "block_total": astate.block_total, "executor_switches": astate.executor_switches,
            })
            if action == HUMAN:
                return human_escalate(ctx, state, mid, events, root="stall",
                                      reason=f"心跳守护：连续{cfg.heartbeat_n_rounds}轮无实质产出")
            if action == SWITCH:
                switch_executor(ctx, state, mid, events,
                                reason=f"心跳守护：连续{cfg.heartbeat_n_rounds}轮无实质产出")
            if action == SPLIT:
                result = _do_split(ctx, state, mid, events, None)
                if result == "split_ok":
                    return DONE
                astate.reason = f"心跳守护：连续{cfg.heartbeat_n_rounds}轮无实质产出；模块无法拆分"
                return human_escalate(ctx, state, mid, events, root="stall", reason=astate.reason)
            if action == UPGRADE_MODEL:
                _upgrade_model(ctx, state, mid, events)
            continue  # retry / pro 兜底 → 同一 executor 下一轮（或已换 executor）

        # 4) auditor 判定
        astate.auditor_round += 1
        events.emit("auditor.round.start", module=mid, detail={"auditor_round": astate.auditor_round})
        aout = _auditor_round(auditor_driver, ctx, state, events, mid, astate.auditor_round, exec_id, cfg)
        if aout.status == "interrupted":
            raise RunInterrupted(f"模块 {mid} 第 {astate.auditor_round} 轮 auditor 被中断")
        budget.record(mid, aout.tokens)
        state.budget_used_tokens += aout.tokens
        astate.tokens_used += aout.tokens
        verdict = aout.verdict or "block"
        # 判定解析失败（2026-08-31 减负修复）：auditor 判定文本措辞未被解析器识别时，
        # 不再误判为 block 触发整模块 executor+auditor 重跑，而是轻量重试——只让 auditor
        # 再出一份结构化判定（代码产物未重做，凭证已在【采证】层）。连续多次解析失败
        # 才按 block 走升级链（此时是 auditor 持续不按协议输出，属能力问题）。
        if verdict == "parse_failed":
            if astate.auditor_round < cfg.retry_before_switch + 1:
                # 写重试标记：让 auditor 脚本在下一轮任务书注入明确提示，避免再次格式错误
                try:
                    hint = ctx.module.dir / "tmp" / ".auditor_parse_retry"
                    hint.parent.mkdir(parents=True, exist_ok=True)
                    hint.write_text("retry", encoding="utf-8")
                except OSError:
                    pass
                events.emit("auditor.parse_retry", module=mid, detail={
                    "auditor_round": astate.auditor_round,
                    "reason": aout.reason or "判定解析失败，重跑 auditor 判定（带格式提示）",
                })
                continue  # 只重跑 auditor（executor 产物不动）
            # 多次解析失败 → 降级为 block，走正常升级链（视为 auditor 能力问题）
            verdict = "block"
            root = "self"
        # BUG-002a 修复（2026-08-25）：证据等级门禁——"无实证的 pass 不成立"（三权分立铁律）
        # auditor 判 pass 但证据等级为 L3（静态推演）或未声明 → 验收依据缺失，直接回人复核。
        # 不走 retry/switch 升级链：这是 auditor 能力/证据问题，不是 executor 交付问题。
        if verdict == "pass" and cfg.audit_require_evidence and aout.evidence_level in ("L3", "", "N/A"):
            events.emit("auditor.no_evidence_pass", module=mid, detail={
                "evidence_level": aout.evidence_level or "缺失",
                "confidence": aout.confidence,
                "evidence": list(aout.evidence or []),
                "reason": aout.reason or "",
            })
            return human_escalate(
                ctx, state, mid, events, root="self",
                reason=(f"auditor 判 pass 但无实证（证据等级={aout.evidence_level or '缺失'}）——"
                        f"按降级验收协议 L3 静态推演不可作验收依据，回人复核。"
                        f"auditor 原话: {aout.reason or ''}"))
        root = aout.root or ("" if verdict == "pass" else "self")
        astate.last_verdict = verdict
        astate.root = root
        astate.reason = aout.reason or ""
        events.emit("auditor.round", module=mid, detail={
            "auditor_round": astate.auditor_round, "verdict": verdict, "root": root,
            "confidence": aout.confidence, "blocker": aout.blocker, "tokens": aout.tokens,
            "evidence_level": aout.evidence_level, "evidence": list(aout.evidence or []),
            "human_pending": list(aout.human_pending or []),  # v1.3：人工验收项（交人+外部AI）
        })
        sync_status = ("done" if verdict == "pass"
                       else ("needs_review" if verdict == "partial" else "blocked"))
        sync_review(spec, astate, status=sync_status,
                    root=root, confidence=aout.confidence, reason=aout.reason, blocker=aout.blocker)

        if verdict == "pass":
            # 出口判定（2026-08-25 重构）：remaining 事实源 = 任务书的 remaining_estimate（planner 拆模块时定的权威值），
            # 程序直接读，不依赖 executor 自报 remaining_lines —— remaining 不是 executor 的活（它只做 first_block）。
            # - remaining None 或缺省 → 本块即全量，模块 done
            # - 0 < remaining ≤ 阈值 → final 续做：剩余不多，同 executor 把剩余做完
            # - remaining > 阈值 → split：拆下一块给新 executor；depth 到底 → 回人（不静默丢活）
            rem = _book_remaining_estimate(spec.book_path)
             # final 续做轮：上一轮已触发 final_block（剩余 ≤ 阈值，同 executor 续做剩余），
            # 本轮 auditor pass 即剩余已做完 → 收官（remaining 静态，不置 0 会死循环）
            if astate.final_round:
                # 2026-08-27 修正：human_pending 是"人工验收项"（GUI/集成，end_gate 交人），**不阻塞 done**。
                # 剩余完整性由 final_block 注入 remaining + auditor 验收项保证（未做 = partial/block 打回），
                # 不再用 human_pending 护栏（会把人工验收项误判成"剩余未做"，前端/外部服务任务频繁误伤）。
                rem = 0
            if rem is not None and rem > 0:
                if rem <= cfg.split_exit_threshold:
                    astate.final_round = True
                    events.emit("module.final_block", module=mid, detail={
                        "remaining_lines": rem, "threshold": cfg.split_exit_threshold,
                        "round": round_no, "reason": "剩余 ≤ 出口阈值：收官轮（final block），同 executor 把剩余做完",
                    })
                    sync_review(spec, astate, status="working",
                                reason=f"本块已 pass；剩余 {rem} 行 ≤ 出口阈值，final 续做：本轮把剩余做完后收工")
                    continue
                if cfg.enable_split and astate.split_depth < cfg.split_max_depth:
                    result = _do_split(ctx, state, mid, events, aout)
                    if result == "split_ok":
                        return DONE   # 父模块标记 split，子模块入队
                    astate.reason = (aout.reason or "pass 但剩余过多，无法拆分") + f"（剩余 {rem} 行）"
                    return human_escalate(ctx, state, mid, events, root="self", reason=astate.reason)
                events.emit("module.split_depth_cap", module=mid, detail={
                    "remaining_lines": rem, "split_depth": astate.split_depth,
                    "max_depth": cfg.split_max_depth,
                })
                return human_escalate(ctx, state, mid, events, root="self",
                                      reason=f"剩余 {rem} 行超过出口阈值({cfg.split_exit_threshold})"
                                             f"但 split 深度已到上限({cfg.split_max_depth})，回人决策")
            astate.ended_at = now_iso()
            state.modules[mid] = "done"
            if mid not in state.completed_order:
                state.completed_order.append(mid)
            # F3fix(2026-09-02): 模块 done 时同步清 needs_human 标记——框架流程解决路径
            # （auditor pass / 归位续跑通过）也要闭环，否则面板「等待处理」永不消失
            if mid in state.needs_human:
                state.needs_human = [m for m in state.needs_human if m != mid]
            events.emit("module.done", module=mid, detail={
                "auditor_round": astate.auditor_round, "root": root,
                "confidence": aout.confidence, "executor_round": astate.executor_round,
                "needs_human_resolved_by": "process",
                "remaining_lines": rem,
            })
            return DONE

        # 4.5) partial → 三态路由：续做（同 executor）/ SPLIT / 回人
        if verdict == "partial":
            action = route_partial(state, mid, cfg, aout.passed_count,
                                   aout.total_count, aout.remaining_items)
            events.emit("module.partial", module=mid, detail={
                "action": action, "passed_count": aout.passed_count,
                "total_count": aout.total_count, "remaining_items": list(aout.remaining_items or []),
                "partial_count": astate.partial_count, "root": root,
            })
            if action == RETRY:
                continue   # 续做：同 executor 下一轮，REVIEW 已带 auditor 反馈
            if action == SPLIT:
                result = _do_split(ctx, state, mid, events, aout)
                if result == "split_ok":
                    return DONE   # 父模块标记 split，子模块入队
                astate.reason = (aout.reason or "partial 无法拆分，回人")
                return human_escalate(ctx, state, mid, events, root=root, reason=astate.reason)
            return human_escalate(ctx, state, mid, events, root=root,
                                  reason=aout.reason or "partial 不能续做/拆分，回人")

        # 5) block → 升级链
        action = route_verdict(state, mid, cfg, root=root, reason=aout.reason or "auditor block")
        events.emit("module.blocked", module=mid, detail={
            "action": action, "root": root, "block_count": astate.block_count,
            "block_total": astate.block_total, "executor_switches": astate.executor_switches,
            "reason": aout.reason,
        })
        if action == HUMAN:
            return human_escalate(ctx, state, mid, events, root=root, reason=aout.reason or "auditor block")
        if action == SWITCH:
            # 交接说明写入 REVIEW（现象/已试办法），随后换 executor（交接三件套）
            write_handover(spec.review_path, {"block_count": str(astate.block_count)},
                           f"现象: {aout.reason or 'auditor block'}\n已试办法: 已按 auditor 反馈重做\n")
            switch_executor(ctx, state, mid, events, reason=aout.reason or "auditor block")
        if action == SPLIT:
            result = _do_split(ctx, state, mid, events, aout)
            if result == "split_ok":
                return DONE   # 父模块标记 split，子模块入队
            astate.reason = (aout.reason or "auditor block") + "：模块无法拆分，回人"
            return human_escalate(ctx, state, mid, events, root=root, reason=astate.reason)
        if action == UPGRADE_MODEL:
            # pro 兜底：仅当前叶子模块，不改变全局默认模型
            _upgrade_model(ctx, state, mid, events)
        # retry → 同一 executor 下一轮
    return state.modules[mid]


def _upgrade_model(ctx: TaskContext, state: RunState, mid: str,
                   events: EventLog) -> None:
    """pro 兜底（v1.0 C3）：仅当前叶子模块升级模型档位，不改变全局默认模型。

    model_tier 0→1（flash→pro）；重置该模块 block_count（给 pro 全新耐心）；
    executor_id 换成 E{n}_pro；emit module.model_upgrade。
    """
    astate = state.ensure(mid)
    astate.model_tier += 1
    astate.block_count = 0
    astate.executor_id = f"E{astate.executor_switches + 1}_pro"
    events.emit("module.model_upgrade", module=mid, detail={
        "model_tier": astate.model_tier,
        "model": ctx.config.model_tiers[astate.model_tier],
        "reason": "split 到上限仍 block，pro 兜底（仅此叶子模块）",
    })


def _do_split(ctx: TaskContext, state: RunState, mid: str,
              events: EventLog, aout: Any = None) -> str:
    """SPLIT 落地（v1.0 C4）：调 split agent 拆模块 → 落地子模块 → 父模块标记 split。

    返回 "split_ok" | "split_failed"。任一步失败（agent 调用 / JSON 校验 / scaffold）
    → emit module.split_failed 并返回 "split_failed"（调用方回人，不硬拆）。
    """
    spec = ctx.modules[mid]
    astate = state.ensure(mid)
    try:
        context = collect_split_context(ctx, state, mid, audit=aout)
        split_json = call_split_agent(ctx, mid, context)
        child_ids = scaffold_children(ctx, mid, split_json)
        generate_shared_context(ctx, mid, split_json, aout)
    except CannotSplitError as e:
        # 2026-08-28（杰哥定稿）：split agent 判定剩余为收尾量级 → 程序化生成「单块」
        # 下放（剩余全量一次做完）。不回原 executor 续做、不回人、不丢活。
        events.emit("module.cannot_split_wrapup", module=mid, detail={
            "parent": mid, "reason": str(e),
            "note": "split agent 判定剩余一轮可完：程序化生成单块下放",
        })
        try:
            split_json = build_wrapup_split_json(ctx, mid, context)
            child_ids = scaffold_children(ctx, mid, split_json)
            generate_shared_context(ctx, mid, split_json, aout)
        except Exception as e2:   # 程序化单块落地失败 → 回人不硬拆
            events.emit("module.split_failed", module=mid, detail={
                "parent": mid, "error": f"CannotSplitError 兜底失败: {type(e2).__name__}: {e2}",
            })
            return "split_failed"
    except Exception as e:   # SplitCallError / SplitJSONError / IO 等 → 回人不硬拆
        events.emit("module.split_failed", module=mid, detail={
            "parent": mid, "error": f"{type(e).__name__}: {e}",
        })
        return "split_failed"
    astate.child_modules = child_ids
    astate.split_depth += 1
    state.modules[mid] = "split"
    # 子模块初始化：pending + parent 指向 + failure 计数
    for cid in child_ids:
        state.modules.setdefault(cid, "pending")
        state.failure_counts.setdefault(cid, 0)
        state.ensure(cid).parent_module = mid
    insert_children_into_order(ctx, mid, child_ids, split_json.get("dependency_map") or {})
    events.emit("module.split", module=mid, detail={
        "parent": mid, "children": child_ids, "split_depth": astate.split_depth,
    })
    return "split_ok"


def _aggregate_parents(ctx: TaskContext, state: RunState, events: EventLog) -> bool:
    """聚合收敛（v1.0 C5/C7）：父模块 split 且全部子模块 done → 父模块自动 done。

    每批结束后调用；一次聚合触发可能解锁下一层（嵌套拆分），故 while 循环收敛。
    """
    changed = False
    while True:
        progressed = False
        for mid, astate in list(state.per_module.items()):
            if not astate.child_modules or state.modules.get(mid) != "split":
                continue
            if not all(state.modules.get(cid) == "done" for cid in astate.child_modules):
                continue
            state.modules[mid] = "done"
            astate.aggregated = True
            astate.ended_at = now_iso()
            if mid not in state.completed_order:
                state.completed_order.append(mid)
            # F3fix: split 容器聚合完成同样清 needs_human（子模块递归解决后父级不应残留）
            if mid in state.needs_human:
                state.needs_human = [m for m in state.needs_human if m != mid]
            events.emit("module.aggregated", module=mid, detail={
                "children": astate.child_modules, "split_depth": astate.split_depth,
                "needs_human_resolved_by": "process",
            })
            progressed = True
            changed = True
        if not progressed:
            break
    return changed


def _merge_child_artifacts(ctx: TaskContext, parent: str, cid: str) -> None:
    """把子模块已有产出合并回父模块（保留产出，不丢活）。

    文件移到父 src/ + 子模块已做追加进父 REVIEW + 父交付物标记 partial（待续做）。
    """
    pmod = ctx.modules[parent]
    cmod = ctx.modules[cid]
    # 1) 文件移到父 src/（保留相对路径，幂等：已存在不覆盖）
    csrc = cmod.dir / "src"
    psrc = pmod.dir / "src"
    psrc.mkdir(parents=True, exist_ok=True)
    if csrc.is_dir():
        for f in sorted(csrc.rglob("*")):
            if f.is_file() and not f.name.startswith("."):
                rel = f.relative_to(csrc)
                dest = psrc / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                if not dest.exists():
                    shutil.move(str(f), str(dest))
                else:
                    # 冲突：重命名为 xxx_from_child，保留双方产出，REVIEW 记录
                    renamed = dest.parent / f"{dest.stem}_from_child{dest.suffix}"
                    shutil.move(str(f), str(renamed))
                    append_done(pmod.review_path, f"[{cid} merge] 冲突重命名: {rel} → {renamed.name}")
    # 2) REVIEW merge：子模块已做追加到父 REVIEW（去「（占位）」行）
    if cmod.review_path.is_file() and pmod.review_path.is_file():
        try:
            cdoc = read_review(cmod.review_path)
            for ln in cdoc.list_done():
                entry = ln.strip().lstrip("- ").strip()
                if entry and "（占位）" not in entry:
                    append_done(pmod.review_path, f"[{cid} merge] {entry}")
        except FileNotFoundError:
            pass
    # 3) 父 REVIEW 状态标记 partial（待续做，不丢已并入产出）
    try:
        set_values(pmod.review_path, status="needs_review",
                   detail=f"子模块 {cid} 合并回父（拆分方向错误），已有产出并入父目录")
    except FileNotFoundError:
        pass


def _check_merge_back(ctx: TaskContext, state: RunState, mid: str,
                      events: EventLog) -> bool:
    """拆解可逆（v1.0 C6）：子模块连续 partial 达阈值 → 整体合并回父，保留产出。

    触发子模块没有 parent → False；should_merge_back 阈值命中 → 把父的全部
    子模块合并回父（拆分方向错误兜底），父恢复 pending 重新执行。
    """
    astate = state.ensure(mid)
    if not astate.parent_module:
        return False
    if not should_merge_back(state, mid, ctx.config):
        return False
    parent = astate.parent_module
    for cid in list(state.ensure(parent).child_modules):
        if cid in ctx.modules:
            _merge_child_artifacts(ctx, parent, cid)
        # 子模块从 状态/上下文 全部移除（防被重新调度 / 与下次拆分 id 冲突）
        state.modules.pop(cid, None)
        state.per_module.pop(cid, None)
        ctx.modules.pop(cid, None)
        ctx.dependencies.pop(cid, None)
        if cid in ctx.module_order:
            ctx.module_order.remove(cid)
    # 父模块恢复待执行（其子模块列表清空、聚合标记复位）
    state.modules[parent] = "pending"
    pstate = state.ensure(parent)
    pstate.child_modules = []
    pstate.aggregated = False
    events.emit("module.merge_back", module=parent, detail={
        "failed_child": mid, "partial_count": astate.partial_count,
        "preserved": "child artifacts moved to parent",
    })
    return True


def _executor_round(driver: Optional[AgentDriver], ctx: TaskContext, state: RunState,
                    mid: str, round_no: int, exec_id: str, cfg: RunConfig) -> DriverOutcome:
    if driver is None:
        raise RunnerInputError("executor_driver 未配置（CLI 默认使用 demo 驱动，见 --executor-cmd）")
    actx = AgentContext(module=ctx.modules[mid], run_id=state.run_id, role="executor",
                        round_no=round_no, executor_id=exec_id, task_root=ctx.task_root,
                        mode=cfg.mode, env=_default_env(ctx))
    # v1.3 fix（2026-08-27）：final_block 收官轮——注入 remaining 作为本轮必做目标
    if _module_final_round(state, mid):
        _inject_final_block(actx, ctx.modules[mid])
    try:
        return driver.run_round(actx)
    except RunInterrupted:
        raise
    except Exception as e:  # 驱动崩溃 → agent_error（进升级链）
        return DriverOutcome(status="error", root="self",
                             reason=f"executor 驱动异常: {type(e).__name__}: {e}")


def _backoff_delay(attempt: int) -> float:
    """指数退避（客观环境错误重试用）：10s → 30s → 90s → 270s，带 ±20% 抖动。"""
    base = 10 * (3 ** (attempt - 1))
    import random as _r
    return base * (0.8 + 0.4 * _r.random())


def _auditor_round(driver: Optional[AgentDriver], ctx: TaskContext, state: RunState,
                   events: EventLog, mid: str, round_no: int, exec_id: str,
                   cfg: RunConfig) -> DriverOutcome:
    if driver is None:
        raise RunnerInputError("auditor_driver 未配置（CLI 默认使用 demo 驱动，见 --auditor-cmd）")
    actx = AgentContext(module=ctx.modules[mid], run_id=state.run_id, role="auditor",
                        round_no=round_no, executor_id=exec_id, task_root=ctx.task_root,
                        mode=cfg.mode, env=_default_env(ctx))

    # 独立 auditor 超时通过驱动层承担（ScriptedAgentDriver.timeout；此处注入 env 供驱动选用）
    timeout_s = float(getattr(cfg, "timeouts_auditor_seconds", 900) or 900)
    actx.env["FW_AUDITOR_TIMEOUT"] = str(timeout_s)

    # v1.3 fix（2026-08-27）：final_block 收官轮——auditor 也要知道本轮验收的是剩余部分
    if _module_final_round(state, mid):
        _inject_final_block(actx, ctx.modules[mid])

    def _valid(o: DriverOutcome) -> bool:
        v = getattr(o, "verdict", ""); r = getattr(o, "root", "")
        c = getattr(o, "confidence", 0.0)
        ok_v = v in ("pass", "partial", "block")
        # root 仅 block 时必须合法分类；pass/partial 时允许为空（lh 既有语义）
        ok_r = r in ("self", "upstream", "contract") or (v in ("pass", "partial") and not r)
        try:
            ok_c = 0.0 <= float(c) <= 1.0
        except (TypeError, ValueError):
            ok_c = False
        # 格式硬性要求：verdict/confidence 必可解析，root 按 verdict 状态放宽
        return ok_v and ok_r and ok_c

    try:
        aout = driver.run_round(actx)
    except RunInterrupted:
        raise
    except Exception as e:
        return DriverOutcome(status="ok", verdict="block", root="self",
                             reason=f"auditor 驱动异常: {type(e).__name__}: {e}")
    if _valid(aout):
        return aout
    if aout.status == "error" and aout.detail.get("audit_timeout"):
        return aout  # 超时直接进升级（不重试）
    # 格式不合法 → 自动重跑一次（附格式纠正语义）
    events.emit("auditor.format_invalid", module=mid, detail={
        "auditor_round": round_no, "verdict": str(aout.verdict),
        "root": str(aout.root), "confidence": str(aout.confidence),
        "blocker": str(aout.blocker)[:80],
    })
    try:
        repaired = driver.run_round(actx)
    except RunInterrupted:
        raise
    except Exception as e:
        return DriverOutcome(status="ok", verdict="block", root="self",
                             reason=f"auditor 驱动异常(重试): {type(e).__name__}: {e}")
    if _valid(repaired):
        repaired.reason = (getattr(repaired, "reason", "") or "") + " [格式修复重试成功]"
        events.emit("auditor.format_repaired", module=mid,
                    detail={"auditor_round": round_no})
        return repaired
    events.emit("auditor.format_failed", module=mid,
                detail={"auditor_round": round_no})
    return DriverOutcome(status="error", root="self",
                         reason="auditor 输出格式不合法（判定四段不可机器解析），重试后仍失败",
                         detail={"audit_format_failed": True})


def _default_env(ctx: TaskContext) -> Dict[str, str]:
    import os
    return {"PATH": os.environ.get("PATH", "")}


def _result(ctx: TaskContext, state: RunState, events: EventLog,
            budget: BudgetGate, hook: Optional[IntegrationHook], extra: Any, t0: float,
            status: str, exit_reason: str, exit_code: int,
            payload: Optional[Dict[str, Any]] = None) -> RunnerResult:
    # 终态收官：仅真正完成（all_modules_done）时注册表置 complete + 刷新 updated_at。
    # interrupted/needs_human/stopped 等终态保持 active（dashboard 仍可跟随/归档，resume 续跑）。
    # 幂等；失败仅告警不阻塞（dashboard 挂了不能挡跑任务）。
    if status == "complete":
        complete_run(state.run_id)
    res = RunnerResult(
        ok=(exit_code == 0),
        status=status,
        exit_reason=exit_reason,
        run_id=state.run_id,
        task_root=ctx.task_root,
        checkpoint=ctx.snapshot_path(),
        completed=list(state.completed_order),
        needs_human=list(state.needs_human),
        failed=sorted({mid for mid, st in state.per_module.items()
                       if st.block_total > 0 or st.executor_switches > 0}),
        tokens_used=state.budget_used_tokens,
        duration_s=time.monotonic() - t0,
        seq_events=events.last_seq,
        config=ctx.config.to_dict(),
        modules={mid: _module_summary(state, mid) for mid in ctx.module_order},
        integration=dict(state.integration),
        payload=dict(payload or {}),
    )
    return res


def _module_summary(state: RunState, mid: str) -> Dict[str, Any]:
    astate = state.ensure(mid)
    return {
        "status": state.modules.get(mid, "pending"),
        "executor_round": astate.executor_round,
        "auditor_round": astate.auditor_round,
        "executor_id": astate.executor_id,
        "executor_switches": astate.executor_switches,
        "block_total": astate.block_total,
        "root": astate.root,
        "reason": astate.reason,
        "last_verdict": astate.last_verdict,
        "needs_human": mid in state.needs_human,
        "tokens": astate.tokens_used,
    }
