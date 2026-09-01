"""fw-integrate 报告：集成验收报告（IntegrationCheckReport）+ 完成报告（md）+ integration.jsonl 事件。

- IntegrationCheckReport：一次集成验收的机器可解析结果 —— 三大程序检查 + 基线对照 +
  errors/warnings/notes + summary（验收1/2 的载体）。
- run_checks()：组合全部检查项（受 integration.check.* 开关控制）。
- build_completion_report()：全部通过时的**完成报告**（markdown，含任务信息/检查结果/
  基线匹配缺失清单/end_gate 决定），是验收3 "完成报告"的载体。
- append_integration_event()：向 总日志/integration.jsonl 追加 integration.check 事件
  （与 fw-runner append_integration_log 同构；run_id/seq 从快照与既有事件续接）。
"""
from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .baseline import BaselineResult, check_baseline
from .checks import CheckResult, check_data_dependency, check_data_format, check_interfaces
from .context import IntegrateContext, IntegrateInputError

INTEGRATION_REL = "总日志/integration.jsonl"


def _now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------- 报告模型

@dataclass
class IntegrationCheckReport:
    """一次集成验收的完整结果（机器可解析；CLI/runner 钩子/tests 消费）。"""

    task_root: Path
    ok: bool
    interface: CheckResult
    data_format: CheckResult
    data_dependency: CheckResult
    baseline: BaselineResult
    notes: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)   # integration.check.* = false 跳过的检查

    @property
    def errors(self) -> List[str]:
        out: List[str] = []
        for cr in (self.interface, self.data_format, self.data_dependency):
            for f in cr.errors:
                out.append(f.message)
        out.extend(f"预测基线缺失: {b.item}" for b in self.baseline.items
                   if b.status == "missing")
        out.extend(f"预测基线违反(will_not_have 命中): {b.item}" for b in self.baseline.items
                   if b.status == "violation")
        return out

    @property
    def warnings(self) -> List[str]:
        out: List[str] = []
        for cr in (self.interface, self.data_format, self.data_dependency):
            for f in cr.warnings:
                out.append(f.message)
        return out

    def summary(self) -> Dict[str, Any]:
        """摘要（runner 钩子 IntegrationReport.summary 用；验收1/2 的机器断言点）。"""
        return {
            "interface": self.interface.to_dict(),
            "data_format": self.data_format.to_dict(),
            "data_dependency": self.data_dependency.to_dict(),
            "baseline": self.baseline.to_dict(),
            "errors": self.errors,
            "warnings": self.warnings,
            "matched": self.baseline.matched,
            "missing": self.baseline.missing,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_root": str(self.task_root),
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
            "notes": list(self.notes),
            "skipped": list(self.skipped),
            "interface": self.interface.to_dict(),
            "data_format": self.data_format.to_dict(),
            "data_dependency": self.data_dependency.to_dict(),
            "baseline": self.baseline.to_dict(),
            "summary": self.summary(),
            "generated_at": _now_iso(),
        }


# ---------------------------------------------------------------- 组合入口

def run_checks(ic: IntegrateContext) -> IntegrationCheckReport:
    """组合全部程序检查 + 基线对照。受 effective.integration.check.* 开关控制：
    interface_duplicate=false 跳过接口检查；prediction_baseline=false 跳过基线对照；
    cross_module_data_dependency=false 跳过依赖检查（数据格式为运行时固有职责，无开关）。
    """
    on = ic.all_checks_on()
    skipped: List[str] = []
    notes: List[str] = []

    interface = CheckResult(name="interface", ok=True)
    if on["interface"]:
        interface = check_interfaces(ic)
    else:
        skipped.append("interface")
        notes.append("integration.check.interface_duplicate=false，本次跳过接口匹配检查")

    data_format = check_data_format(ic)   # 始终执行（运行时产物格式校验为集成职责）

    data_dependency = CheckResult(name="data_dependency", ok=True)
    if on["cross_module_data_dependency"]:
        data_dependency = check_data_dependency(ic)
    else:
        skipped.append("data_dependency")
        notes.append("integration.check.cross_module_data_dependency=false，本次跳过跨模块数据依赖检查")

    baseline = BaselineResult(ok=True)
    if on["prediction_baseline"]:
        baseline = check_baseline(ic)
    else:
        skipped.append("baseline")
        notes.append("integration.check.prediction_baseline=false，本次跳过预测基线对照")

    ok = interface.ok and data_format.ok and data_dependency.ok and baseline.ok
    return IntegrationCheckReport(
        task_root=ic.task_root, ok=ok,
        interface=interface, data_format=data_format,
        data_dependency=data_dependency, baseline=baseline,
        notes=notes, skipped=skipped,
    )


# ---------------------------------------------------------------- integration.jsonl

def _read_integration_events(task_root: Path) -> List[Dict[str, Any]]:
    p = task_root / INTEGRATION_REL
    if not p.is_file():
        return []
    out: List[Dict[str, Any]] = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
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
        pass
    return out


def next_integration_seq(task_root: Path) -> int:
    """integration.jsonl 下一可用 seq（既有 seq 最大值 + 1；无则 1）。"""
    max_seq = 0
    for ev in _read_integration_events(task_root):
        try:
            max_seq = max(max_seq, int(ev.get("seq") or 0))
        except (TypeError, ValueError):
            continue
    return max_seq + 1


def append_integration_event(task_root: Path, report: IntegrationCheckReport,
                             end_gate: str = "auto",
                             run_id: str = "") -> Path:
    """向 总日志/integration.jsonl 追加一行 integration.check（与 fw-runner 事件同构）。

    seq 从 integration.jsonl 既有事件续接（独立于 dispatch.jsonl 的 seq 域，文档说明）。
    """
    p = task_root / INTEGRATION_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    seq = next_integration_seq(task_root)
    line = {
        "ts": _now_iso(),
        "seq": seq,
        "run_id": run_id,
        "event": "integration.check",
        "end_gate": end_gate,
        "detail": {"status": "passed" if report.ok else "failed",
                   "ok": report.ok,
                   "errors": report.errors,
                   "warnings": report.warnings,
                   "summary": report.summary()},
    }
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
        f.flush()
    return p


# ---------------------------------------------------------------- 完成报告

def build_completion_report(ic: IntegrateContext, report: IntegrationCheckReport,
                            *, archive_result: Optional[Dict[str, Any]] = None,
                            status: str = "completed") -> str:
    """全部通过时的完成报告（markdown）。status: completed | confirmed | needs_confirmation。"""
    t = ic.effective.get("task") or {}
    lines = [
        "# 完成报告 —— 集成验收全部通过",
        "",
        f"> 任务：{ic.task_name} ｜ 任务根：{ic.task_root}",
        f"> 生成时间：{_now_iso()} ｜ run_id：{ic.snapshot.get('run_id') or '-'}",
        f"> 状态：{status}"
        + ("（end_gate=always，等待人工确认后归档）" if status == "needs_confirmation"
           else "（end_gate=always，人工已确认并归档）" if status == "confirmed"
           else "（end_gate=auto，已自动归档）"),
        "",
        "## 模块状态（快照）",
    ]
    modules = ic.snapshot.get("modules") or {}
    for mid in ic.module_order:
        st = modules.get(mid, "?")
        lines.append(f"- {mid}：{st}（REVIEW status={ic.review_status(mid) or 'unknown'}）")
    lines += [
        "",
        "## 集成检查结果",
        f"- 接口匹配：{'通过' if report.interface.ok else '失败'}（"
        f"error={len(report.interface.errors)} warning={len(report.interface.warnings)}）",
        f"- 数据格式：{'通过' if report.data_format.ok else '失败'}（"
        f"error={len(report.data_format.errors)} info={len(report.data_format.infos)}）",
        f"- 跨模块数据依赖：{'通过' if report.data_dependency.ok else '失败'}（"
        f"error={len(report.data_dependency.errors)} warning={len(report.data_dependency.warnings)}）",
        f"- 预测基线对照：{'通过' if report.baseline.ok else '失败'}",
        "",
        "## 预测基线 —— 匹配清单（will_have matched）",
    ]
    matched = report.baseline.matched
    if matched:
        for b in report.baseline.items:
            if b.kind == "will_have" and b.status == "matched":
                ev = "; ".join(b.evidence) if b.evidence else "（关键词级命中）"
                lines.append(f"- [x] {b.item}  —— 证据: {ev}")
    else:
        lines.append("- （无）")
    lines += ["", "## 预测基线 —— 缺失/违反清单（will_have missing / will_not_have violation）"]
    missing = report.baseline.missing
    violations = report.baseline.violations
    if not missing and not violations:
        lines.append("- （无 —— 全部基线满足）")
    for b in report.baseline.items:
        if b.kind == "will_have" and b.status == "missing":
            lines.append(f"- [ ] 缺失：{b.item}")
    for b in report.baseline.items:
        if b.kind == "will_not_have" and b.status == "violation":
            ev = "; ".join(b.evidence) if b.evidence else "（关键词级命中）"
            lines.append(f"- [!] 违反 will_not_have：{b.item}  —— 证据: {ev}")
    lines += ["", "## 基线 clean（will_not_have 未命中）"]
    clean = report.baseline.clean
    lines.append("- " + ("；".join(clean) if clean else "（无 will_not_have 声明）"))
    lines += ["", "## end_gate 决定"]
    if status == "completed":
        lines.append(f"- end_gate={ic.end_gate}（auto）：无异常，自动完成并归档。")
    elif status == "confirmed":
        lines.append(f"- end_gate={ic.end_gate}（always）：全部通过，人工已确认，完成并归档。")
    else:
        lines.append(f"- end_gate={ic.end_gate}（always）：全部通过，等待人工确认（不自动归档）。")
    if archive_result:
        lines += ["", "## 归档结果"]
        lines.append(f"- 旧路径：{archive_result.get('old_path')}")
        lines.append(f"- 新路径：{archive_result.get('new_path')}")
        lines.append(f"- 归档时间：{archive_result.get('archived_at')}")
        lines.append(f"- 归档说明：{archive_result.get('archived_mark')}")
    lines.append("")
    return "\n".join(lines)
