"""fw-budget 管理操作：人工加预算（add-budget）/ 放弃归档（archive）/ 续跑（resume）。

三权分立边界：premit 管理操作（加预算/归档）是**人工（任务管理者/真人）**动作，
本模块只提供落地命令与原子防护，不代替真人决策；executor 永不自定验收标准（需求3铁律）。

- add_budget：改 task.yaml 的 budget.max_tokens（fs 原子写 = 同目录临时文件 + fsync +
  os.replace）。保留原文件注释头（fw-scaffold effective 版本带说明头），只替换 YAML 体。
  修改后任务书仍通过 fw-protocol 校验（max_tokens 为合法整数），resume 时 runner
  重新加载有效。已知限制：task.yaml 指纹变化后，再跑 fw-scaffold 会因 expected 版本
  防护拒绝（需 --force）——文档如实标注（task.yaml 属任务输入，不被 scaffold 覆盖）。
- archive：放弃 → 把整个任务根 move 到 <父>/archived/<任务目录名>-<时间戳>/，
  move 前把快照 status 标记为 archived（原子写），move 后在新位置写 ARCHIVE.md。
  归档后 fw-budget resume/status 会拒绝（防止误续跑已放弃任务）。
- resume：人工加预算后的续跑包装 —— 校验未归档 → （可选加预算）→ 用事件流/dsh
  token-meter 重建 BudgetGate（累计消耗灌回，resume 不失忆）→ fw_runner.runner.run(
  resume=True, budget_gate=gate)。已完成模块不重跑（runner 快照机制保证）。
"""
from __future__ import annotations

import datetime as _dt
import json
import shutil
import sys as _sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import yaml

_FW1 = Path(__file__).resolve().parent.parent.parent
for _d in ("fw-runner", "fw-protocol", "fw-scaffold"):
    _p = str((_FW1 / _d).resolve())
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

from fw_runner.budget_hook import BudgetGate  # noqa: E402
from fw_runner.drivers import AgentDriver  # noqa: E402
from fw_runner.events import EventLog  # noqa: E402
from fw_runner.runner import run as runner_run  # noqa: E402

from .gate_state import build_budget_gate, load_effective_budget  # noqa: E402
from .meter import DshTokenMeter, TokenMeter  # noqa: E402
from .report import SNAPSHOT_REL  # noqa: E402

ENCODING = "utf-8"

# fw-scaffold manifest 名（expected 版本防护用；本模块只读检查不写）
_MANIFEST_NAME = ".scaffold-manifest.json"


class BudgetManageError(Exception):
    """管理操作失败（输入非法 / 状态不允许 / IO）。"""


def _now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _atomic_write_text(path: Path, content: str) -> None:
    """fs 原子写（等价 dsh fs 原子写；不用 Redis/外部锁，同目录 rename 即并发防护）。"""
    import os
    import tempfile
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".fwbgt")
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


def _split_header(text: str) -> Tuple[str, str]:
    """把 task.yaml 文本拆成 [注释头, YAML 体]（保留 fw-scaffold 说明头）。"""
    lines = text.splitlines()
    idx = 0
    while idx < len(lines):
        stripped = lines[idx].strip()
        if stripped == "" or stripped.startswith("#"):
            idx += 1
            continue
        break
    header = "\n".join(lines[:idx])
    body = "\n".join(lines[idx:])
    return (header + "\n" if header else "", body)


# ---------------------------------------------------------------- add-budget

@dataclass
class BudgetUpdate:
    """一次人工加预算的结果。"""

    task_root: Path
    old_max_tokens: int
    new_max_tokens: int
    warn_at: float
    stop_at: float
    per_module_max_tokens: Optional[int]
    updated_at: str
    reason: str = ""
    file: str = ""

    def to_dict(self) -> Dict:
        return {
            "task_root": str(self.task_root),
            "old_max_tokens": self.old_max_tokens,
            "new_max_tokens": self.new_max_tokens,
            "warn_at": self.warn_at,
            "stop_at": self.stop_at,
            "per_module_max_tokens": self.per_module_max_tokens,
            "updated_at": self.updated_at,
            "reason": self.reason,
            "file": self.file,
        }


def add_budget(task_root: str | Path, new_max_tokens: int, *,
               reason: str = "") -> BudgetUpdate:
    """人工加预算：更新 task.yaml 的 budget.max_tokens（fs 原子写，保留注释头）。

    变更后任务书重新通过 fw-protocol 校验（resume 时 runner 会重新加载有效版本）。
    """
    root = Path(task_root)
    task_yaml = root / "task.yaml"
    if not task_yaml.is_file():
        raise BudgetManageError(f"任务根找不到 task.yaml：{task_yaml}")
    if not isinstance(new_max_tokens, int) or new_max_tokens < 1:
        raise BudgetManageError(f"new_max_tokens 必须为正整数，收到 {new_max_tokens!r}")

    text = task_yaml.read_text(encoding=ENCODING)
    header, body = _split_header(text)
    if not body.strip():
        raise BudgetManageError(f"task.yaml 缺少 YAML 体（{task_yaml}）")
    try:
        doc = yaml.safe_load(body)
    except yaml.YAMLError as e:
        raise BudgetManageError(f"task.yaml 解析失败: {e}") from e
    if not isinstance(doc, dict) or not isinstance(doc.get("budget"), dict):
        raise BudgetManageError("task.yaml 缺少 budget 段（应先 fw-scaffold 生成 effective 版本）")

    b = doc["budget"]
    old_max = int(b.get("max_tokens") or 1_000_000)
    b["max_tokens"] = new_max_tokens
    # 单模块上限语义同步：effective 版本里 per_module_max_tokens 的默认值 = old_max（fw-scaffold
    # 写的默认补齐）。若它仍等于旧全局值，视为"默认跟随全局"，加预算时同步提升；
    # 用户显式配置的独立单模块上限保持不动（尊重显式配置，文档已标注该语义）。
    if b.get("per_module_max_tokens") == old_max:
        b["per_module_max_tokens"] = new_max_tokens
    # 单模块上限若大于新全局预算，协议仅给 warning（不阻塞）；这里不做越权修改
    body_new = yaml.safe_dump(doc, allow_unicode=True, sort_keys=False,
                              default_flow_style=False, indent=2, width=120)
    new_text = (header + body_new) if header else body_new
    _atomic_write_text(task_yaml, new_text)

    return BudgetUpdate(
        task_root=root,
        old_max_tokens=old_max,
        new_max_tokens=new_max_tokens,
        warn_at=float(b.get("warn_at") if b.get("warn_at") is not None else 0.7),
        stop_at=float(b.get("stop_at") if b.get("stop_at") is not None else 1.0),
        per_module_max_tokens=(b.get("per_module_max_tokens")
                               if isinstance(b.get("per_module_max_tokens"), int) else None),
        updated_at=_now_iso(),
        reason=reason,
        file=str(task_yaml),
    )


# ---------------------------------------------------------------- archive

@dataclass
class ArchiveResult:
    """一次放弃归档的结果。"""

    old_path: Path
    new_path: Path
    archived_at: str
    reason: str
    snapshot_status: str
    run_id: str
    archived_mark: Path

    def to_dict(self) -> Dict:
        return {
            "old_path": str(self.old_path),
            "new_path": str(self.new_path),
            "archived_at": self.archived_at,
            "reason": self.reason,
            "snapshot_status": self.snapshot_status,
            "run_id": self.run_id,
            "archived_mark": str(self.archived_mark),
        }


def _load_snapshot(root: Path) -> Optional[Dict]:
    p = root / SNAPSHOT_REL
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding=ENCODING))
    except (OSError, json.JSONDecodeError):
        return None


def _write_snapshot(root: Path, doc: Dict) -> None:
    _atomic_write_text(root / SNAPSHOT_REL, json.dumps(doc, ensure_ascii=False, indent=2) + "\n")


def archive(task_root: str | Path, *, reason: str = "",
            to_dir: Optional[str | Path] = None) -> ArchiveResult:
    """放弃 → 归档：快照标记 archived → 整个任务根 move 到 <父>/archived/<目录名>-<时间戳>/。

    归档是人工放弃动作（不回滚、不自动恢复）；归档后 resume/status 拒绝，防止误续跑。
    """
    root = Path(task_root).expanduser().resolve()
    if not root.is_dir():
        # 原路径已不在：检查归档区是否已有同名前缀目录（双归档友好报错）
        archived_dir = Path(to_dir).expanduser().resolve() / "archived" if to_dir else root.parent / "archived"
        if archived_dir.is_dir():
            twins = [d for d in archived_dir.iterdir() if d.is_dir() and d.name.startswith(root.name + "-")]
            if twins:
                raise BudgetManageError(
                    f"任务已归档，不能重复归档（已归档到 {sorted(twins)[-1]}）: {root}")
        raise BudgetManageError(f"任务根不存在: {root}")
    snap = _load_snapshot(root)
    if snap is not None and snap.get("status") == "archived":
        raise BudgetManageError(f"任务已归档，不能重复归档: {root}")

    # 1) 快照标记 archived（原子写；move 前完成，保证任何时刻可判定）
    if snap is not None:
        snap["status"] = "archived"
        snap["cause"] = "budget_abandoned"
        snap["archived_at"] = _now_iso()
        snap["archived_reason"] = reason
        snap["note"] = "人工放弃归档（fw-budget archive）；不续跑"
        _write_snapshot(root, snap)

    # 2) move 到归档区
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    parent = Path(to_dir).expanduser().resolve() if to_dir else root.parent
    archive_dir = parent / "archived"
    archive_dir.mkdir(parents=True, exist_ok=True)
    new_path = archive_dir / f"{root.name}-{stamp}"
    if new_path.exists():
        # 时间戳冲突（同一秒归档两次）→ 加毫秒后缀
        new_path = archive_dir / f"{root.name}-{stamp}-{_dt.datetime.now().strftime('%f')}"
    shutil.move(str(root), str(new_path))

    # 3) 归档区写 ARCHIVE.md（真实证据：原始路径/时间/原因/预算状态摘要）
    budget_brief = ""
    try:
        b = load_effective_budget(new_path)
        budget_brief = (f"max_tokens={b.get('max_tokens')} "
                        f"warn_at={b.get('warn_at')} stop_at={b.get('stop_at')} "
                        f"per_module_max_tokens={b.get('per_module_max_tokens')}")
    except Exception:
        budget_brief = "（预算不可读）"
    mark = new_path / "ARCHIVE.md"
    mark.write_text(
        f"# 归档说明（fw-budget archive）\n\n"
        f"- 归档时间: {_now_iso()}\n"
        f"- 原路径: {root}\n"
        f"- 新路径: {new_path}\n"
        f"- 原因: {reason or '（未填）'}\n"
        f"- 快照状态: {snap.get('status') if snap else 'no_snapshot'}\n"
        f"- run_id: {snap.get('run_id') if snap else ''}\n"
        f"- 预算: {budget_brief}\n",
        encoding=ENCODING)

    return ArchiveResult(
        old_path=root, new_path=new_path, archived_at=_now_iso(), reason=reason,
        snapshot_status=(snap.get("status") if snap else "no_snapshot"),
        run_id=(snap.get("run_id") if snap else ""),
        archived_mark=mark,
    )


# ---------------------------------------------------------------- resume

@dataclass
class ResumeAdvice:
    """resume 前的预警信息（不阻塞；帮助人工决定是否先加预算）。"""

    would_stop_now: bool
    used: int
    max_tokens: int
    message: str


def resume_advice(task_root: str | Path,
                  meter: Optional[TokenMeter] = None) -> ResumeAdvice:
    """resume 前检查：用当前预算 + 累计消耗判定是否会立即再停（提示加预算）。"""
    root = Path(task_root)
    try:
        gate = build_budget_gate(root, meter=meter or DshTokenMeter(root))
    except Exception as e:
        return ResumeAdvice(would_stop_now=False, used=0, max_tokens=0,
                            message=f"预算不可读（{type(e).__name__}: {e}）")
    st = gate.check()
    msg = (f"当前预算会被立即再次硬停（used={gate.used} >= max_tokens*stop_at"
           f"({gate.max_tokens}*{gate.stop_at})）。建议先 fw-budget add-budget 加预算。"
           if st.stop else
           f"当前预算可续跑（used={gate.used}/{gate.max_tokens}，ratio={st.ratio:.1%}）。")
    return ResumeAdvice(would_stop_now=bool(st.stop), used=gate.used,
                        max_tokens=gate.max_tokens, message=msg)


def _assert_resumable(root: Path) -> None:
    """resume 前置校验：任务根存在 / 快照存在 / 未归档。"""
    if not root.is_dir():
        archived_dir = root.parent / "archived"
        if archived_dir.is_dir():
            twins = [d for d in archived_dir.iterdir()
                     if d.is_dir() and d.name.startswith(root.name + "-")]
            if twins:
                raise BudgetManageError(
                    f"任务已归档（{sorted(twins)[-1]}），放弃后不可续跑: {root}")
        raise BudgetManageError(f"任务根不存在: {root}")
    snap = _load_snapshot(root)
    if snap is None:
        raise BudgetManageError("找不到 总日志/快照.json（从未运行过？先 fw-runner run 再 resume）")
    if snap.get("status") == "archived":
        raise BudgetManageError(f"任务已归档（{root}/ARCHIVE.md），放弃后不可续跑")
    if snap.get("status") == "complete":
        # 已完成的任务也可 resume（幂等 no-op），允许但提示
        return


def resume(task_root: str | Path, *,
           extra_max_tokens: Optional[int] = None,
           reason: str = "",
           executor_driver: Optional[AgentDriver] = None,
           auditor_driver: Optional[AgentDriver] = None,
           budget_gate: Optional[BudgetGate] = None,
           meter: Optional[TokenMeter] = None,
           overrides: Optional[Mapping[str, Any]] = None,
           mode: str = "speed_first",
           event_log: Optional[EventLog] = None,
           ) -> Any:
    """人工加预算后的续跑包装：未归档校验 → 可选加预算 → 重建闸门（累计消耗）→ runner resume。

    返回 fw_runner RunnerResult。已完成模块不重跑（runner 快照机制）。
    """
    root = Path(task_root).expanduser().resolve()
    _assert_resumable(root)

    if extra_max_tokens is not None:
        add_budget(root, extra_max_tokens, reason=reason or "resume 加预算")

    if budget_gate is None:
        budget_gate = build_budget_gate(root, meter=meter or DshTokenMeter(root))

    return runner_run(
        root,
        overrides=overrides,
        mode=mode,
        resume=True,
        executor_driver=executor_driver,
        auditor_driver=auditor_driver,
        budget_gate=budget_gate,
        event_log=event_log,
    )


def run_first(task_root: str | Path, *,
              executor_driver: Optional[AgentDriver] = None,
              auditor_driver: Optional[AgentDriver] = None,
              budget_gate: Optional[BudgetGate] = None,
              meter: Optional[TokenMeter] = None,
              overrides: Optional[Mapping[str, Any]] = None,
              mode: str = "speed_first",
              event_log: Optional[EventLog] = None,
              ) -> Any:
    """首次运行包装：注入真实 BudgetGate（fw-runner CLI 默认 Null 闸门，预算闸门归本模块）。

    与 resume 的唯一差别是 resume=False（无快照；从零开始的第一轮也可用事件流账本
    恢复历史 —— 若之前跑过 Null 闸门轮次，meter 会把它记入，避免重复记账）。
    """
    root = Path(task_root).expanduser().resolve()
    if not root.is_dir():
        raise BudgetManageError(f"任务根不存在: {root}")
    if budget_gate is None:
        budget_gate = build_budget_gate(root, meter=meter or DshTokenMeter(root))
    return runner_run(
        root,
        overrides=overrides,
        mode=mode,
        resume=False,
        executor_driver=executor_driver,
        auditor_driver=auditor_driver,
        budget_gate=budget_gate,
        event_log=event_log,
    )


def assert_not_archived(task_root: str | Path) -> None:
    """供 status 等命令调用：归档任务拒绝读取（防误续跑）。"""
    _assert_resumable(Path(task_root))
