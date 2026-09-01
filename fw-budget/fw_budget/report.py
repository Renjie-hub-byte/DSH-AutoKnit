"""预算状态报告 —— 需求5 验收②"信息完备"的机器可解析载体。

报告内容（status / 抛人信息 / auditor 复现都消费它）：
- 闸门判定：used / max_tokens / warn_at / stop_at / per_module_max_tokens / ratio /
  warned / stop / message（来自 fw-runner BudgetGate.check）
- token 汇总：meter 报告（source= dsh|fallback、per_module、ranking 排行）
- 进度信息：completed（完成模块，来自快照 completed_order）/ unfinished（未完成模块）/
  tried（每模块已试轮数 executor_round，来自快照 per_module）/ needs_human（回人模块）
- 事件证据：事件流里最近的 budget.warn / budget.stop 事件（含当时的 ranking 快照）
- phase：ok | warned | stopped（当前闸门相位；warn 与 stop 可叠加，stop 优先展示）

诚实口径：completed/unfinished/tried 来自 总日志/快照.json（runner 的 checkpoint 投影）；
token 来自记账源（本地账本默认；dsh token-meter 接入后 source=dsh）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .gate_state import build_budget_gate, load_effective_budget
from .meter import DshTokenMeter, TokenMeter, summarize

SNAPSHOT_REL = "总日志/快照.json"
DISPATCH_REL = "总日志/dispatch.jsonl"


@dataclass
class BudgetEventEvidence:
    """事件流里的预算证据（warn/stop 时的 budget 快照 + ranking）。"""

    event: str = ""            # budget.warn | budget.stop
    seq: int = 0
    at: str = ""
    budget: Dict[str, Any] = field(default_factory=dict)
    ranking: List[Dict] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> Dict:
        return {
            "event": self.event, "seq": self.seq, "at": self.at,
            "budget": dict(self.budget), "ranking": list(self.ranking), "note": self.note,
        }


@dataclass
class BudgetReport:
    """一次完整预算状态报告（机器可解析；natural 摘要由 CLI 层拼装）。"""

    task_root: Path
    phase: str                    # ok | warned | stopped
    gate: Dict[str, Any]          # BudgetStatus.to_dict()
    meter: Dict[str, Any]         # MeterReport.to_dict()
    completed: List[str]
    unfinished: List[str]
    tried: Dict[str, int]         # mid -> executor_round（快照 per_module）
    needs_human: List[str]
    events: List[BudgetEventEvidence]
    snapshot_status: str
    run_id: str
    warning_message: str = ""
    stop_message: str = ""

    def to_dict(self) -> Dict:
        return {
            "task_root": str(self.task_root),
            "phase": self.phase,
            "gate": dict(self.gate),
            "meter": dict(self.meter),
            "completed": list(self.completed),
            "unfinished": list(self.unfinished),
            "tried": dict(self.tried),
            "needs_human": list(self.needs_human),
            "events": [e.to_dict() for e in self.events],
            "snapshot_status": self.snapshot_status,
            "run_id": self.run_id,
            "warning_message": self.warning_message,
            "stop_message": self.stop_message,
        }


def _read_snapshot(task_root: Path) -> Optional[Dict]:
    p = task_root / SNAPSHOT_REL
    if not p.is_file():
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _read_budget_events(task_root: Path) -> List[BudgetEventEvidence]:
    p = task_root / DISPATCH_REL
    out: List[BudgetEventEvidence] = []
    if not p.is_file():
        return out
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict) or ev.get("event") not in ("budget.warn", "budget.stop"):
            continue
        detail = ev.get("detail") or {}
        out.append(BudgetEventEvidence(
            event=str(ev.get("event")),
            seq=int(ev.get("seq") or 0),
            at=str(ev.get("at") or ev.get("ts") or ""),
            budget=dict(detail.get("budget") or {}),
            ranking=list(detail.get("ranking") or []),
            note=str(detail.get("note") or ""),
        ))
    return out


def build_report(task_root: str | Path, meter: Optional[TokenMeter] = None) -> BudgetReport:
    """构建预算状态报告：闸门判定 + token 汇总 + 进度信息 + 事件证据。

    不跑 runner —— 纯读快照/事件流/task.yaml，auditor 可零写入复现。
    相位判定以**当前闸门判定**（累计消耗 vs 预算阈值）为准；事件证据仅作佐证。
    """
    root = Path(task_root)
    # 默认记账源 = DshTokenMeter（真实接入走 dsh，未接入回退本地事件流账本）——
    # build_report 是只读视角，必须带历史累计消耗才能判定当前 warn/stop 相位
    meter = meter if meter is not None else DshTokenMeter(root)
    gate = build_budget_gate(root, meter=meter)
    st = gate.check()
    mrep = summarize(root, meter=meter)

    snap = _read_snapshot(root) or {}
    per_module_snap = snap.get("per_module") or {}
    modules_state = snap.get("modules") or {}
    completed = [str(x) for x in (snap.get("completed_order") or [])]
    tried: Dict[str, int] = {}
    needs_human: List[str] = []
    unfinished: List[str] = []
    # tried 对快照声明过的**所有**模块补全（未试过 = 0），保证"每个模块已试轮数"信息完备
    for mid in modules_state:
        st_dict = per_module_snap.get(mid) or {}
        tried[str(mid)] = int(st_dict.get("executor_round") or 0)
        if str(mid) in (snap.get("needs_human") or []):
            needs_human.append(str(mid))
        if modules_state[mid] != "done" and str(mid) not in completed:
            unfinished.append(str(mid))
    # 事件证据（最近 warn + stop；按 seq 升序保留全部，CLI 摘要只展示最新）
    events = _read_budget_events(root)

    if st.stop:
        phase = "stopped"
    elif st.warned:
        phase = "warned"
    else:
        phase = "ok"

    return BudgetReport(
        task_root=root,
        phase=phase,
        gate=st.to_dict(),
        meter=mrep.to_dict(),
        completed=completed,
        unfinished=unfinished,
        tried=tried,
        needs_human=needs_human,
        events=events,
        snapshot_status=str(snap.get("status") or "no_snapshot"),
        run_id=str(snap.get("run_id") or ""),
        warning_message=(st.message if st.warned and not st.stop else ""),
        stop_message=(st.message if st.stop else ""),
    )


def human_summary(report: BudgetReport) -> str:
    """人类可读摘要（CLI status --json=False 用）。"""
    lines = [
        "fw-budget 预算状态报告",
        f"任务根     : {report.task_root}",
        f"相位       : {report.phase}（ok=未达阈值 / warned=达 warn_at 预警 / stopped=硬停）",
        f"快照状态   : {report.snapshot_status}   run_id={report.run_id or '-'}",
        "",
        f"全局 token : 已用 {report.gate.get('used', 0)} / {report.gate.get('max_tokens', '?')}"
        f"（{report.gate.get('ratio', 0):.1%}）  warn_at={report.gate.get('warn_at')}"
        f"  stop_at={report.gate.get('stop_at')}",
        f"单模块上限 : {report.gate.get('per_module_max_tokens') or '未单独限制'}",
        f"记账来源   : {report.meter.get('source', '?')}"
        f"（dsh=token-meter 真实接入 / fallback=本地事件流账本）",
        f"各模块消耗排行（降序）:",
    ]
    ranking = report.meter.get("ranking") or []
    if ranking:
        for r in ranking:
            lines.append(f"  - {r['module']}: {r['tokens']} tokens")
    else:
        lines.append("  （无 token 记账，空账本）")
    lines += [
        "",
        f"完成模块   : {', '.join(report.completed) if report.completed else '（无）'}",
        f"未完成模块 : {', '.join(report.unfinished) if report.unfinished else '（无）'}",
        f"回人模块   : {', '.join(report.needs_human) if report.needs_human else '（无）'}",
        f"已试轮数(executor_round): "
        + ("；".join(f"{k}={v}" for k, v in sorted(report.tried.items())) if report.tried else "（无）"),
    ]
    if report.warning_message:
        lines += ["", f"⚠ 预算预警: {report.warning_message}"]
    if report.stop_message:
        lines += ["", f"⛔ 预算硬停: {report.stop_message}",
                  "   处理建议：fw-budget add-budget <任务根> --max-tokens <新预算> 后 "
                  "fw-budget resume <任务根> 续跑（已完成不重跑）；或 fw-budget archive <任务根> 放弃归档。"]
    if report.events:
        lines += ["", "预算事件证据（事件流）:",
                  *[f"  seq={e.seq} {e.event}  at={e.at}  {e.budget.get('message','')}"
                    for e in report.events]]
    return "\n".join(lines)
