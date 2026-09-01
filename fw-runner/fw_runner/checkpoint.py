"""checkpoint 快照（总日志/快照.json）—— sessionProjections checkpoint 本地形态。

- 每 checkpoint_every 模块完成写一次 + 关键状态转移（回人/预算停/中断/完成）都写
- fs 原子写（同目录临时 + fsync + os.replace），并发安全不需要锁
- resume：读快照恢复 RunState → 已完成模块不重跑（其 executor/auditor 不再被调用），
  计数（executor_round/auditor_round/failure_counts/last_seq/budget）从快照续接
- schema_version: 4（runner 快照协议；fw-scaffold 初始化为 2，runner 首次写升级为 3，
  再经 v1.0 升级到 4——per_module 增 split 字段 + running→pending 崩溃恢复）
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from .context import TaskContext, SNAPSHOT_REL
from .io_utils import atomic_write_text
from .model import ModuleAgentState, RunState, now_iso

SNAPSHOT_SCHEMA_VERSION = 4
SNAPSHOT_SCHEMA_V3 = 3  # v0.4 旧快照版本（G4：可识别并加载，缺字段默认兜底）


def read_snapshot(task_root: str | Path) -> Optional[Dict[str, Any]]:
    p = Path(task_root) / SNAPSHOT_REL
    if not p.is_file():
        return None
    import json
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def snapshot_schema_version(doc: Dict[str, Any]) -> int:
    """识别快照 schema_version（G4；缺字段/非法值兜底为 0，代表未知旧快照）。"""
    try:
        v = doc.get("schema_version")
        return int(v) if v is not None else 0
    except (TypeError, ValueError):
        return 0


def snapshot_to_state(ctx: TaskContext, doc: Dict[str, Any]) -> RunState:
    """快照 → 内存 RunState（resume 用；缺字段容错）。

    G3 崩溃恢复：快照中 running 的模块重置为 pending（重新执行，比人工排查便宜）。
    G4 旧快照：识别 schema_version（含 v3），缺字段经 ModuleAgentState.from_dict 默认兜底。
    """
    _ = snapshot_schema_version(doc)  # G4：识别旧快照版本（兼容加载，不拒绝）
    st = RunState()
    st.run_id = str(doc.get("run_id") or "")
    st.status = str(doc.get("status") or "running")
    st.cause = str(doc.get("cause") or "")
    modules = doc.get("modules") or {}
    st.modules = {str(k): str(v) for k, v in modules.items() if isinstance(v, str)}
    for mid in ctx.module_order:
        st.modules.setdefault(mid, "pending")
    for mid in list(st.modules):
        if st.modules[mid] == "running":
            st.modules[mid] = "pending"  # G3：崩溃恢复，重新执行
    fail = doc.get("failure_counts") or {}
    for mid in ctx.module_order:
        st.failure_counts[mid] = int(fail.get(mid) or 0)
    st.needs_human = [str(x) for x in (doc.get("needs_human") or []) if str(x) in ctx.module_order]
    st.completed_order = [str(x) for x in (doc.get("completed_order") or [])
                          if str(x) in ctx.module_order]
    st.budget_used_tokens = int(doc.get("budget_used_tokens") or 0)
    st.last_seq = int(doc.get("last_seq") or 0)
    per = doc.get("per_module") or {}
    for mid in ctx.module_order:
        st.per_module[mid] = ModuleAgentState.from_dict(per.get(mid) or {})
    return st


def build_snapshot(ctx: TaskContext, state: RunState, status: str,
                   cause: str, note: str = "") -> Dict[str, Any]:
    """RunState → 快照文档。"""
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "run_id": state.run_id,
        "task": ctx.task_name,
        "updated_at": now_iso(),
        "status": status,
        "cause": cause,
        "note": note,
        "modules": dict(state.modules),
        "dependencies": {mid: list(deps) for mid, deps in ctx.dependencies.items()},
        "failure_counts": dict(state.failure_counts),
        "per_module": {mid: st.to_dict() for mid, st in state.per_module.items()},
        "needs_human": list(state.needs_human),
        "completed_order": list(state.completed_order),
        "budget_used_tokens": state.budget_used_tokens,
        "last_seq": state.last_seq,
    }


def write_checkpoint(ctx: TaskContext, state: RunState, status: str,
                     cause: str, note: str = "") -> Path:
    """原子写快照；返回快照路径。"""
    doc = build_snapshot(ctx, state, status, cause, note)
    atomic_write_text(ctx.snapshot_path(),
                      _json(doc))
    return ctx.snapshot_path()


def _json(obj: Any) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"
