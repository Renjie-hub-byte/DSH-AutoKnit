"""升级链（需求4 + v1.0）：auditor block → 回同 executor → N 次后换 executor（交接三件套）
→ SPLIT（递归拆模块）→ pro 兜底（仅当前叶子模块）→ 上限回人。

路由语义（v1.0 简化版 定稿 + 本框架落地的确定性约定）：
- verdict=pass                    → done
- root ∈ {upstream, contract}     → human（失败根因分流：直接抛人，不重试）
- block_count < retry_before_switch → retry（回同一 executor，REVIEW.md 已带 auditor 反馈）
- block_count ≥ retry_before_switch 且 executor_switches < max_executor_switches → switch
  （换新 executor；block_count 清零，新 executor 获得 retry_before_switch 次耐心）
- switch 用尽且 enable_split 且 split_depth < split_max_depth → split（调 split agent 拆模块）
- split 到上限且 enable_fallback_model 且 model_tier 未到顶 → upgrade（pro 兜底，仅此叶子模块，
  不改变全局默认模型）
- 其余                           → human（上限回人，信息完备：完成/未完成/已试/token）

upgrade 链顺序（不可乱）：retry → switch → split → upgrade → human。

卡死（executor_max_rounds 超限 / agent 崩溃）走 route_stuck：等效 block/self 但
不给 retry（避免同一 executor 死循环），直接 switch 或 human。

交接三件套 = REVIEW.md（含判定与交接说明）+ contract.yaml + 交付说明.md；
换 executor 时写入模块 logs/（豁免区），新 executor 开工先读 REVIEW.md。

可靠性补丁（进度落档 / 换人续作）：
- 换 executor 时把「进度指针」（前任 已完成/剩余）写进 REVIEW.md 进度指针 小节，
  并把「这是前任做到的位置，从『剩余』继续，不要重做已完成部分」的提示词片段
  拼进交接 bundle（bundle 已含前任 交付说明.md 全文）；新 executor 据此续作不从头。
- 回人（finalize_human）时同样落 REVIEW 进度指针，真人接手信息完备。
"""
from __future__ import annotations

from typing import Optional

from .context import TaskContext
from .events import EventLog
from .io_utils import atomic_write_text
from .model import RunConfig, RunState, now_iso
from .progress import (
    STALE_MARKER,
    progress_briefing,
    progress_snapshot,
    upsert_section_file,
    write_progress,
)
from .review import handover_bundle, set_values, write_handover

# 路由动作
DONE = "done"
RETRY = "retry"
SWITCH = "switch"
SPLIT = "split"            # 🆕 v1.0：递归拆分（调 split agent 拆模块）
UPGRADE_MODEL = "upgrade"  # 🆕 v1.0：pro 兜底（仅当前叶子模块）
HUMAN = "human"
RETRY_BACKOFF = "retry_backoff"   # 客观环境错误（限流/断网/5xx）：不退避换人，只限次退避重试


def route_verdict(state: RunState, mid: str, cfg: RunConfig,
                  root: str, reason: str = "") -> str:
    """auditor block 后的升级链路由（含 block_count/block_total 记账）。

    返回 DONE/RETRY/SWITCH/SPLIT/UPGRADE_MODEL/HUMAN 之一。pass 判定在 runner 侧直接
    done，本函数处理 block 分支（root=upstream/contract → HUMAN 分流）。
    """
    astate = state.ensure(mid)
    astate.block_count += 1
    astate.block_total += 1
    astate.last_verdict = "block"
    astate.root = root or "self"
    astate.reason = reason or ""
    if root in ("upstream", "contract"):
        return HUMAN                       # 失败根因分流：直接抛人不重试
    if astate.block_count < cfg.retry_before_switch:
        return RETRY                       # 回同一 executor（REVIEW 已带反馈）
    if astate.executor_switches < cfg.max_executor_switches:
        return SWITCH                      # 换新 executor
    if cfg.enable_split and astate.split_depth < cfg.split_max_depth:
        return SPLIT                       # switch 用尽 → 递归拆模块
    if cfg.enable_fallback_model and astate.model_tier < len(cfg.model_tiers) - 1:
        return UPGRADE_MODEL               # split 到上限 → pro 兜底（仅此叶子模块）
    return HUMAN                           # 上限回人


def route_partial(state: RunState, mid: str, cfg: RunConfig,
                  passed_count: int, total_count: int,
                  remaining_items: list) -> str:
    """partial 判定路由（v2 贪心）：看剩余交付物数，不看次数。

    - 剩余 ≤ retry_remaining_threshold → 原 executor 续做（剩得不多，做完就好）
    - 剩余 > retry_remaining_threshold → 立即 SPLIT（剩太多，跳过无谓续做，拆给下一个）
    - partial_count 只作防死循环兜底：连续续做超 max_partial_rounds 次仍做不完 → 回人

    语义对齐杰哥诉求："剩得不多让原 executor 做完，剩得太多叫 split 继续"。
    """
    astate = state.ensure(mid)
    astate.partial_count += 1
    astate.last_verdict = "partial"
    remaining = len(remaining_items) if remaining_items else max(0, total_count - passed_count)
    # 防死循环兜底：连续 partial 太多次仍做不完 → 回人
    if astate.partial_count >= cfg.max_partial_rounds:
        return HUMAN
    # 剩余不多 → 原 executor 续做
    if remaining <= cfg.retry_remaining_threshold:
        return RETRY
    # 剩余多 → 立即 split（跳过无谓续做）
    if cfg.enable_split and astate.split_depth < cfg.split_max_depth:
        return SPLIT
    return HUMAN


def should_merge_back(state: RunState, mid: str, cfg: RunConfig) -> bool:
    """子模块连续 partial 达到阈值 → 合并回父（保留子模块已有产出）。"""
    astate = state.ensure(mid)
    return astate.partial_count >= cfg.split_merge_after_fails


def route_stuck(state: RunState, mid: str, cfg: RunConfig, reason: str,
                root: str = "self") -> str:
    """卡死（executor_max_rounds 超限 / agent 崩溃 / 心跳卡死以外的硬卡）→ 等效 block/self，
    但不给 retry（防同一 executor 死循环）。

    root="upstream" 时（客观环境错误：限流/断网/5xx）→ 返回 RETRY_BACKOFF：
    这是环境问题，换 executor 无用（新 executor 上来照样断），应限次退避重试
    当前回合；退避重试计数在 runner 侧维护（env_upstream_retries）。
    """
    astate = state.ensure(mid)
    if root == "upstream":
        # 环境类：补充退避重试预算，超过预算才回人（不换 executor）
        astate.env_backoffs = getattr(astate, "env_backoffs", 0) + 1
        astate.last_verdict = "block"
        astate.root = "upstream"
        astate.reason = reason or "upstream(限流/断网/5xx)"
        max_env = getattr(cfg, "max_env_retries", 3)
        if astate.env_backoffs < max_env:
            return RETRY_BACKOFF
        return HUMAN
    astate.block_count += 1
    astate.block_total += 1
    astate.last_verdict = "block"
    astate.root = "self"
    astate.reason = reason or "executor_max_rounds"
    if astate.executor_switches < cfg.max_executor_switches:
        return SWITCH
    return HUMAN


def assign_initial_executor(state: RunState, mid: str) -> str:
    astate = state.ensure(mid)
    if not astate.executor_id:
        astate.executor_id = "E1"
    return astate.executor_id


def _progress_pointer_lines(old_executor: str, snap: dict, audience: str) -> list[str]:
    """REVIEW.md 进度指针 小节行（机器可解析：前任 executor / 已完成 / 剩余 / 进度来源）。"""
    return [
        f"- 前任 executor: {old_executor}",
        f"- 已完成: {snap.get('已完成', STALE_MARKER)}",
        f"- 剩余: {snap.get('剩余', STALE_MARKER)}",
        f"- 进度来源: {snap.get('source', '?')}",
        (f"- 交接信息（{audience}）：从「剩余」继续，不要重做已完成的部分；先读 交付说明.md 全文" if audience == "新 executor"
         else f"- 交接信息（{audience}）：以上为 executor 落档进度，未尽事项见 交付说明.md"),
    ]


def switch_executor(ctx: TaskContext, state: RunState, mid: str,
                    events: EventLog, reason: str = "") -> str:
    """换新 executor：executor_switches+1、block_count/stall_count 清零、写交接三件套。

    可靠性补丁（功能B）：交接前把前任 executor 的进度快照固化为
    REVIEW.md 进度指针 + 交付说明.md 进度快照（缺失时从 REVIEW 已做/待办 兜底），
    并把「前任做到的位置 / 从『剩余』继续」提示词拼进交接 bundle —— 新 executor 续作不从头。
    """
    astate = state.ensure(mid)
    old_executor = astate.executor_id or "E1"
    astate.executor_switches += 1
    astate.executor_id = f"E{astate.executor_switches + 1}"
    astate.block_count = 0
    astate.stall_count = 0
    module = ctx.modules[mid]
    try:
        snap = progress_snapshot(module)
        # 前任未在 交付说明.md 落档 → 把兜底进度固化成真实快照（换人后仍可续读）
        if snap.get("source") != "delivery":
            write_progress(module.delivery_path, done=snap.get("已完成", STALE_MARKER),
                           remaining=snap.get("剩余", STALE_MARKER),
                           executor_id=old_executor, round_no=astate.executor_round,
                           source=str(snap.get("source", "REVIEW")))
        if module.review_path.is_file():
            upsert_section_file(module.review_path, "进度指针",
                                _progress_pointer_lines(old_executor, snap, "新 executor"))
    except (OSError, FileNotFoundError):
        snap = {"已完成": STALE_MARKER, "剩余": STALE_MARKER, "source": "best-effort"}
    bundle = progress_briefing(module, old_executor, snap) + handover_bundle(module)
    bundle_path = module.dir / "logs" / f"handover-{astate.executor_id}-{now_iso().replace(':','-')}.md"
    atomic_write_text(bundle_path, bundle)
    events.emit("executor.switch", module=mid, detail={
        "executor_id": astate.executor_id, "executor_switches": astate.executor_switches,
        "handover_bundle": str(bundle_path), "reason": reason,
        "progress": {"已完成": snap.get("已完成", ""), "剩余": snap.get("剩余", ""),
                     "source": snap.get("source", "")},
    })
    return astate.executor_id


def finalize_human(ctx: TaskContext, state: RunState, mid: str, events: EventLog,
                   root: str = "", reason: str = "") -> str:
    """模块回人：状态置 needs_human、事件落盘、REVIEW 同步。"""
    astate = state.ensure(mid)
    astate.ended_at = now_iso()
    state.modules[mid] = "needs_human"
    if mid not in state.needs_human:
        state.needs_human.append(mid)
    events.emit("module.needs_human", module=mid, detail={
        "root": root or astate.root, "reason": reason or astate.reason,
        "executor_round": astate.executor_round, "auditor_round": astate.auditor_round,
        "block_total": astate.block_total, "executor_switches": astate.executor_switches,
        "executor_id": astate.executor_id,
    })
    # REVIEW 机器键全量同步（单一写者）：counter（block_total 等）以 route 记账为准
    try:
        sync_review(ctx.modules[mid], astate, status="needs_human",
                    root=root or astate.root, reason=reason or astate.reason)
    except FileNotFoundError:
        pass
    # 可靠性补丁（功能B/回人信息完备）：把 executor 落档进度固化为 REVIEW 进度指针
    try:
        snap = progress_snapshot(ctx.modules[mid])
        if ctx.modules[mid].review_path.is_file():
            upsert_section_file(ctx.modules[mid].review_path, "进度指针",
                                _progress_pointer_lines(astate.executor_id, snap, "真人"))
    except (OSError, FileNotFoundError):
        pass
    return HUMAN


def sync_review(module, astate, status: str, root: str = "", confidence: Optional[float] = None,
                reason: str = "", blocker: str = "") -> None:
    """把 runner 权威状态键写回 REVIEW.md（单一写者；driver 只写内容小节）。

    root 为空时保留 REVIEW 现有 root（不覆盖 auditor 已写回的判定）。
    """
    kv = {
        "status": status,
        "executor_round": str(astate.executor_round),
        "auditor_round": str(astate.auditor_round),
        "executor_id": astate.executor_id,
        "executor_switches": str(astate.executor_switches),
        "block_count": str(astate.block_count),
        "block_total": str(astate.block_total),
        "stall_count": str(astate.stall_count),
    }
    if root:
        kv["root"] = root
    if confidence is not None:
        try:
            kv["confidence"] = f"{float(confidence):.3f}".rstrip("0").rstrip(".")
        except (TypeError, ValueError):
            kv["confidence"] = str(confidence)
    if reason:
        kv["detail"] = reason
    set_values(module.review_path, **kv)
