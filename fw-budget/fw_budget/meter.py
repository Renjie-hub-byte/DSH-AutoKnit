"""fw-budget token 记账源 —— dsh token-meter 跨会话统计的本地等价物 + 真实接入适配点。

设计定位（v0.4 / 需求5）：token **汇总**归 dsh token-meter（底部免费能力，跨会话统计），
框架只做**闸门逻辑**（warn_at 70% 预警 / stop_at 100% 硬停 / per_module_max_tokens
单模块上限 / 加预算 resume / 放弃归档）。

本模块提供三件事：
1. `TokenMeter` 协议：total() / per_module() / ranking() —— 任何记账源的最小契约。
2. `EventLogTokenMeter`：**本地等价物** —— 从 总日志/dispatch.jsonl 事件流归集 token。
   fw-runner 每轮（executor.round.done / auditor.round）都把 `DriverOutcome.tokens`
   （dsh token-meter 对接钩子）写进事件 detail，因此事件流是**逐轮精确的 token 记账账本**，
   与 runner 内存 `state.budget_used_tokens` 同源。这是未接入 dsh 时框架自洽运行的记账源，
   同时为 resume 提供"把历史累计消耗灌回 BudgetGate"的依据（见 gate_state.build_budget_gate）。
3. `DshTokenMeter`：**dsh token-meter 真实接入的适配点（stub）**。真实接入位置与调用方式
   已在类注释与 docs/budget-spec.md 中如实标注；默认行为回退到 EventLogTokenMeter，
   保证没有 dsh 接入时框架仍可跑（与 fw-runner 的 NullBudgetGate 思路一致）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Protocol


class TokenMeter(Protocol):
    """记账源最小契约：总量 / 每模块 / 排行。"""

    def total(self) -> int:
        ...

    def per_module(self) -> Dict[str, int]:
        ...

    def ranking(self) -> List[Dict]:
        ...


# 事件类型里携带 token 消耗的成员（fw-runner 每轮 emit；detail.tokens 为 DriverOutcome.tokens）
_TOKEN_EVENTS = ("executor.round.done", "auditor.round")

# 总日志目录（与 fw-runner context.SNAPSHOT_REL 同源；此处只读不写）
_DISPATCH_REL = "总日志/dispatch.jsonl"


def _read_dispatch_lines(task_root: str | Path) -> List[Dict]:
    p = Path(task_root) / _DISPATCH_REL
    if not p.is_file():
        return []
    out: List[Dict] = []
    try:
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    out.append(obj)
    except OSError:
        return []
    return out


class EventLogTokenMeter:
    """本地等价物：从 dispatch.jsonl 事件流归集每模块 token 消耗（跨会话只读汇总）。

    统计口径（与 fw-runner BudgetGate.record 同源）：
    - 同一 run_id 事件流内，executor.round.done 与 auditor.round 的 detail.tokens 全额计入。
    - README/文档如实标注：这是"本地账本"形态；接入 dsh token-meter 后由
      DshTokenMeter 替换（见适配点），口径不变（仍是每轮 DriverOutcome.tokens）。
    """

    def __init__(self, task_root: str | Path) -> None:
        self.task_root = Path(task_root)
        self._per_module: Dict[str, int] = {}
        self._total = 0
        self._events_seen = 0
        self._run_ids: List[str] = []
        self._scan()

    def _scan(self) -> None:
        per: Dict[str, int] = {}
        run_ids: List[str] = []
        seen = 0
        for ev in _read_dispatch_lines(self.task_root):
            ev_name = ev.get("event", "")
            if ev_name not in _TOKEN_EVENTS:
                continue
            rid = str(ev.get("run_id") or "")
            if rid and rid not in run_ids:
                run_ids.append(rid)
            mid = str(ev.get("module") or "")
            if not mid:
                continue
            try:
                tokens = int((ev.get("detail") or {}).get("tokens") or 0)
            except (TypeError, ValueError):
                tokens = 0
            if tokens <= 0:
                continue
            per[mid] = per.get(mid, 0) + tokens
            seen += 1
        self._per_module = per
        self._total = sum(per.values())
        self._events_seen = seen
        self._run_ids = run_ids

    def total(self) -> int:
        return self._total

    def per_module(self) -> Dict[str, int]:
        return dict(self._per_module)

    def ranking(self) -> List[Dict]:
        return sorted(
            ({"module": mid, "tokens": used} for mid, used in self._per_module.items()),
            key=lambda x: x["tokens"], reverse=True,
        )

    def events_seen(self) -> int:
        """计入了多少条带 token 的事件（审计可核对口径）。"""
        return self._events_seen

    def run_ids(self) -> List[str]:
        return list(self._run_ids)


class DshTokenMeter:
    """dsh token-meter 真实接入的**适配点（stub）**——需求5"token 汇总按 dsh token-meter
    跨会话统计设计（底部免费能力）"的落点。

    真实接入位置（接入时替换 `_query_dsh` 的实现，其余框架代码不变）：
    - 概念：dsh 的 token-meter = 底部免费能力，按会话（sessions.fork 派生的 agent 会话）
      记录 token 消耗，跨会话可汇总（一次任务 = 1 个 run_id 的 executor/auditor 多个会话）。
    - 调用方式（目标形态，按 dsh 实际 CLI/API 适配）：
          dsh meter --session <run_id> --json      # 汇总该 run 全部会话
          dsh meter --session <run_id> --module <mid>   # 单模块
      （真实命令名/参数以 dsh 平台为准；此处仅标注语义位置。）
    - 兜底：`_query_dsh()` 返回 None（未接入 / 查询失败）→ 回退 EventLogTokenMeter
      （本地账本），与 fw-runner NullBudgetGate 的"未就绪不卡主循环"思路一致。
    """

    def __init__(self, task_root: str | Path, dsh_context: Optional[Dict] = None,
                 fallback: Optional[TokenMeter] = None) -> None:
        self.task_root = Path(task_root)
        self.dsh_context = dict(dsh_context or {})
        self.fallback: TokenMeter = fallback or EventLogTokenMeter(task_root)
        self.source: str = "fallback"          # dsh | fallback（报告里标注数据来源）
        self._dsh_total: Optional[int] = None
        self._dsh_per_module: Dict[str, int] = {}
        self._try_dsh()

    # ---- 适配点：唯一需要按 dsh 平台改的接口 ----------
    def _query_dsh(self) -> Optional[Dict]:
        """向 dsh token-meter 查询跨会话 token 统计。

        返回 {total: int, per_module: {mid: tokens}, raw: ...} 或 None（未接入/失败）。
        当前为 stub：恒返回 None → 走 fallback。接入实现示例见 docs/budget-spec.md §适配点。
        """
        return None
    # -------------------------------------------------

    def _try_dsh(self) -> None:
        try:
            data = self._query_dsh()
        except Exception:
            data = None
        if not isinstance(data, dict) or "total" not in data:
            self.source = "fallback"
            return
        self.source = "dsh"
        self._dsh_total = int(data.get("total") or 0)
        self._dsh_per_module = {str(k): int(v) for k, v in (data.get("per_module") or {}).items()}

    def total(self) -> int:
        if self.source == "dsh":
            return self._dsh_total or 0
        return self.fallback.total()

    def per_module(self) -> Dict[str, int]:
        if self.source == "dsh":
            return dict(self._dsh_per_module)
        return self.fallback.per_module()

    def ranking(self) -> List[Dict]:
        per = self.per_module()
        return sorted(
            ({"module": mid, "tokens": used} for mid, used in per.items()),
            key=lambda x: x["tokens"], reverse=True,
        )

    def source_name(self) -> str:
        """数据来源标注：'dsh'（真实接入）| 'fallback'（本地等价物）。"""
        return self.source


@dataclass
class MeterReport:
    """一次记账汇总（status/resume 报告复用；机器可解析）。"""

    source: str = "fallback"                       # dsh | fallback
    total: int = 0
    per_module: Dict[str, int] = field(default_factory=dict)
    ranking: List[Dict] = field(default_factory=list)
    events_seen: int = 0
    run_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "source": self.source,
            "total": self.total,
            "per_module": dict(self.per_module),
            "ranking": list(self.ranking),
            "events_seen": self.events_seen,
            "run_ids": list(self.run_ids),
        }


def summarize(task_root: str | Path, meter: Optional[TokenMeter] = None) -> MeterReport:
    """对任务根做一次 token 汇总（默认用 DshTokenMeter：有 dsh 走 dsh，否则本地账本）。"""
    m = meter if meter is not None else DshTokenMeter(task_root)
    if isinstance(m, EventLogTokenMeter):
        return MeterReport(source="fallback", total=m.total(), per_module=m.per_module(),
                           ranking=m.ranking(), events_seen=m.events_seen(), run_ids=m.run_ids())
    if isinstance(m, DshTokenMeter):
        return MeterReport(source=m.source_name(), total=m.total(), per_module=m.per_module(),
                           ranking=m.ranking(),
                           events_seen=(m.fallback.events_seen()
                                        if isinstance(m.fallback, EventLogTokenMeter) else 0),
                           run_ids=(m.fallback.run_ids()
                                    if isinstance(m.fallback, EventLogTokenMeter) else []))
    return MeterReport(source="custom", total=m.total(), per_module=m.per_module(),
                       ranking=m.ranking())
