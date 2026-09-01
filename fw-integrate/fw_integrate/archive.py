"""fw-integrate 完成归档（需求6 验收3 的"归档"）：全部通过 → 完成报告 + 归档。

设计（复用 fw-budget 归档机制，遵守"只作钩子调用、不改已审计模块"）：
- `complete_and_archive()` 流程：
  1. 加载集成上下文（require_complete=True：快照须 complete，防未跑完就归档）。
  2. 跑全部检查（run_checks）→ 有 error → IntegrateFailed（exit 2，不归档，回人）。
  3. 按 end_gate 分流：
     - auto：写入 integration.check 事件 → 调 `fw_budget.manage.archive`（已审计机制：
       快照原子标记 + 目录 move 到 archived/ + ARCHIVE.md）→ 在新位置（归档树）把快照
       cause 修正为 completed（fw-budget 的 archive 语义面向"放弃"，省略 cause 为
       budget_abandoned；完成归档在归档树内回写 cause/note 以消除歧义，机制本身复用）
       → 写入 完成报告.md。
     - always：只写完成报告（status=needs_confirmation），不自动归档，exit 2 请人工确认。
  4. 返回 CompletionArchiveResult（机器可解析）。
- 边界注意：**归档不得在 runner 钩子内发生**（runner 钩子后还会写快照/integration 日志，
  移目录会破坏后续写）；因此归档只由 complete / run 收尾阶段执行，钩子只判不归档。
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

# 复用兄弟包路径（context 已引导 sys.path；此处兜底再确认）
_FW1_ROOT = Path(__file__).resolve().parent.parent.parent
for _d in ("fw-budget", "fw-runner", "fw-protocol", "fw-scaffold"):
    _p = str((_FW1_ROOT / _d).resolve())
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fw_budget.manage import archive as fw_budget_archive  # noqa: E402

from .context import IntegrateContext, IntegrateInputError, load_integrate_context  # noqa: E402
from .report import (  # noqa: E402
    IntegrationCheckReport, append_integration_event,
    build_completion_report, run_checks,
)

ENCODING = "utf-8"
SNAPSHOT_REL = "总日志/快照.json"
COMPLETION_REPORT_NAME = "完成报告.md"


class IntegrateFailed(Exception):
    """集成验收失败（存在 error）：不归档，回人。message 含错误清单。"""


@dataclass
class CompletionArchiveResult:
    """一次完成归档的结果（机器可解析）。status: completed | needs_confirmation | failed。"""

    ok: bool
    status: str                       # completed | needs_confirmation | failed
    task_root: Path
    archived_path: Optional[Path]     # completed 时 = 归档新路径
    completion_report: Optional[Path]
    archive: Optional[Dict[str, Any]]
    report: IntegrationCheckReport
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status,
            "task_root": str(self.task_root),
            "archived_path": str(self.archived_path) if self.archived_path else None,
            "completion_report": str(self.completion_report) if self.completion_report else None,
            "archive": self.archive,
            "checks": self.report.to_dict(),
            "message": self.message,
        }


def _atomic_write_text(path: Path, content: str) -> None:
    """fs 原子写（等价 dsh fs 原子写；同目录 rename 即并发防护，不用外部锁）。"""
    import os
    import tempfile
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".fwinteg")
    try:
        with os.fdopen(fd, "w", encoding=ENCODING, newline="\n") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_snapshot(root: Path) -> Optional[Dict]:
    p = root / SNAPSHOT_REL
    if not p.is_file():
        return None
    try:
        doc = json.loads(p.read_text(encoding=ENCODING))
        return doc if isinstance(doc, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _mark_completed_in_archived_tree(archived_root: Path, run_id: str) -> None:
    """归档树内快照 cause 修正：fw-budget archive 面向"放弃"会把 cause 写成 budget_abandoned；
    完成归档在此把 cause/note 改为 completed（原子写，只影响归档树，不触碰 fw-budget 语义）。"""
    snap = _read_snapshot(archived_root)
    if snap is None:
        return
    snap["status"] = "archived"
    snap["cause"] = "completed"
    snap["archived_reason"] = snap.get("archived_reason") or "集成验收全部通过，任务完成归档"
    snap["note"] = "任务完成归档（fw-integrate complete_and_archive）；不续跑"
    _atomic_write_text(archived_root / SNAPSHOT_REL, json.dumps(snap, ensure_ascii=False, indent=2) + "\n")


def complete_and_archive(task_root: str | Path, *, reason: str = "",
                         force_confirmation: bool = False) -> CompletionArchiveResult:
    """全部通过 → 完成报告 + 归档（end_gate 分流）。

    force_confirmation=True 时即使 end_gate=auto 也先出报告请人工确认不自动归档（测试钩子）。
    """
    ic = load_integrate_context(task_root, require_complete=True)
    report = run_checks(ic)
    if not report.ok:
        msg = "集成验收失败（不归档，回人）：\n" + "\n".join(f"  - {e}" for e in report.errors)
        raise IntegrateFailed(msg)

    run_id = str(ic.snapshot.get("run_id") or "")

    # end_gate=always（或强制确认）→ 只写完成报告，人工确认后另行归档
    if ic.end_gate == "always" or force_confirmation:
        report_path = ic.task_root / COMPLETION_REPORT_NAME
        _atomic_write_text(report_path, build_completion_report(
            ic, report, status="needs_confirmation"))
        return CompletionArchiveResult(
            ok=True, status="needs_confirmation", task_root=ic.task_root,
            archived_path=None, completion_report=report_path, archive=None,
            report=report,
            message="end_gate=always：集成检查全部通过，等待人工确认（完成报告已写入，未自动归档）",
        )

    # end_gate=auto：写 integration.check 事件 → fw-budget archive（已审计机制）→ 完成报告
    append_integration_event(ic.task_root, report, end_gate=ic.end_gate, run_id=run_id)
    ar = fw_budget_archive(ic.task_root,
                           reason=reason or "集成验收全部通过，任务完成归档（fw-integrate）")
    new_root = Path(ar.new_path)
    # 归档树内快照 cause 修正 + 完成报告写入归档树
    _mark_completed_in_archived_tree(new_root, run_id)
    report_path = new_root / COMPLETION_REPORT_NAME
    _atomic_write_text(report_path, build_completion_report(
        ic, report, status="completed",
        archive_result={"old_path": str(ar.old_path), "new_path": str(ar.new_path),
                        "archived_at": ar.archived_at, "archived_mark": str(ar.archived_mark)}))

    return CompletionArchiveResult(
        ok=True, status="completed", task_root=ic.task_root,
        archived_path=new_root, completion_report=report_path,
        archive={
            "old_path": str(ar.old_path), "new_path": str(ar.new_path),
            "archived_at": ar.archived_at, "archived_mark": str(ar.archived_mark),
            "snapshot_status": ar.snapshot_status, "run_id": ar.run_id,
        },
        report=report,
        message=f"集成验收全部通过，任务已归档：{new_root}",
    )


def confirm_and_archive(task_root: str | Path, *, reason: str = "") -> CompletionArchiveResult:
    """end_gate=always 的人工确认入口：快照 needs_confirmation → 检查全通过 → 完成报告 + 归档。

    真实链路上 fw-runner（round_004 已审计）在 end_gate=always 且全部模块 + 集成钩子通过时，
    把快照写成 status=needs_confirmation（exit 2 等待人工拍板），**不会**写 complete 快照；
    因此 fw-integrate complete（要求快照 complete）无法为这类任务出完成报告/归档。
    本函数补上闭环：人工确认（--confirm）后，检查全通过 → 写入 passed 事件 → 复用
    fw-budget 归档机制 → 归档树内 cause=completed → 写 完成报告.md（status=confirmed）。

    使用（对应任务书 end_gate=always 语义）：
        fw-runner run 任务根            # 模块跑完 + 集成钩子通过 → needs_confirmation
        fw-integrate confirm 任务根    # 人工确认 → 完成报告 + 归档（exit 0）
    auto 门任务请用 complete_and_archive（本函数对 end_gate=auto 也兼容，语义=人工主动收口）。
    """
    ic = load_integrate_context(task_root, require_complete=False)
    snap_status = str(ic.snapshot.get("status") or "")
    if snap_status not in ("needs_confirmation", "complete"):
        raise IntegrateInputError(
            f"confirm 仅用于待人工确认/已完成任务（快照 status={snap_status}，需 "
            f"needs_confirmation 或 complete；end_gate=always 任务由 fw-runner 置为 "
            f"needs_confirmation 后确认，auto 任务请用 complete）")

    report = run_checks(ic)
    if not report.ok:
        msg = "集成验收失败（不归档，回人）：\n" + "\n".join(f"  - {e}" for e in report.errors)
        raise IntegrateFailed(msg)

    run_id = str(ic.snapshot.get("run_id") or "")
    append_integration_event(ic.task_root, report, end_gate=ic.end_gate, run_id=run_id)
    ar = fw_budget_archive(ic.task_root,
                           reason=reason or "人工确认（end_gate=always）后完成归档（fw-integrate confirm）")
    new_root = Path(ar.new_path)
    _mark_completed_in_archived_tree(new_root, run_id)
    report_path = new_root / COMPLETION_REPORT_NAME
    _atomic_write_text(report_path, build_completion_report(
        ic, report, status="confirmed",
        archive_result={"old_path": str(ar.old_path), "new_path": str(ar.new_path),
                        "archived_at": ar.archived_at, "archived_mark": str(ar.archived_mark)}))

    return CompletionArchiveResult(
        ok=True, status="confirmed", task_root=ic.task_root,
        archived_path=new_root, completion_report=report_path,
        archive={
            "old_path": str(ar.old_path), "new_path": str(ar.new_path),
            "archived_at": ar.archived_at, "archived_mark": str(ar.archived_mark),
            "snapshot_status": ar.snapshot_status, "run_id": ar.run_id,
        },
        report=report,
        message=f"人工确认完成，任务已归档：{new_root}",
    )

