"""progress —— 由快照 + 事件流拼出模块级进度（只读计算）。

产出：
{
  "total": int, "done": int, "percent": float,
  "status": str, "note": str, "completed_order": [mid],
  "modules": {mid: {status, done, executor_round, auditor_round,
                    started_at, ended_at, roles, duration_s, tokens_used}}
}
"""

from .enums import ROLES


def _int_or(data, key, default=0):
    try:
        return int(data.get(key, default))
    except (TypeError, ValueError):
        return default


def build_progress(snapshot, events):
    """拼模块进度。snapshot 可为空（此时返回空进度骨架）。"""
    if snapshot is None:
        return {
            "total": 0,
            "done": 0,
            "percent": 0.0,
            "status": "unknown",
            "note": "",
            "completed_order": [],
            "modules": {},
        }

    modules = {}
    module_ids = snapshot.module_ids
    for mid in module_ids:
        detail = snapshot.per_module.get(mid, {}) or {}
        done = snapshot.modules.get(mid) in ("done",)
        role_seen = []
        for ev in events:
            if ev.module == mid and ev.role and ev.role not in role_seen:
                role_seen.append(ev.role)
        # 稳定排序角色
        role_seen.sort(key=lambda r: ROLES.index(r) if r in ROLES else 99)
        modules[mid] = {
            "status": str(snapshot.modules.get(mid, "") or ""),
            "done": done,
            "executor_round": _int_or(detail, "executor_round"),
            "auditor_round": _int_or(detail, "auditor_round"),
            "started_at": str(detail.get("started_at", "") or ""),
            "ended_at": str(detail.get("ended_at", "") or ""),
            "roles": role_seen,
            "duration_s": snapshot.module_duration(mid),
            "tokens_used": snapshot.module_tokens(mid),
        }

    done = len(snapshot.done_module_ids)
    total = len(module_ids)
    percent = round(done * 100.0 / total, 1) if total else 0.0

    return {
        "total": total,
        "done": done,
        "percent": percent,
        "status": snapshot.status,
        "note": snapshot.note,
        "completed_order": list(snapshot.completed_order) or snapshot.done_module_ids,
        "modules": modules,
    }
