"""state —— 面板状态拼装（对齐 dsh.panel.state data_shape）。

data_shape.response:
  stage        enum[planning, exec, audit, split, idle]
  roles        list[enum[planner, executor, auditor]]
  consumption  {token_input:int, token_output:int, cache_hit:bool, duration_s:float}
  pending      list（待决策信息）
  （extendable：另附 progress、updated_at）
"""

from .enums import STAGES, DEFAULT_STAGE, normalize_roles
from .events import latest_role
from .consumption import build_consumption
from .progress import build_progress
from .pending import read_human_pending


def derive_stage(snapshot, events):
    """推导当前阶段。快照显式给了 stage 则用之；否则按状态启发式推导。"""
    if snapshot is None:
        return DEFAULT_STAGE
    if snapshot.stage in STAGES:
        return snapshot.stage

    status = snapshot.status or ""
    # 有 pending 待决策 → 阻塞等待真人（idle）
    if snapshot.needs_human:
        return "idle"
    if snapshot.all_modules_done:
        return "idle"
    if status in ("created", "pending"):
        return "planning"
    if status in ("planning",):
        return "planning"

    # 按最近一个有角色归属的事件推导
    role = latest_role(events)
    if role == "planner":
        return "planning"
    if role == "auditor":
        return "audit"
    if role == "executor":
        return "exec"

    if status == "running":
        return "exec"
    if status in ("error", "blocked"):
        return "idle"
    return DEFAULT_STAGE


def derive_roles(snapshot, events):
    """推导活跃角色列表。快照显式给了 roles 则用之；否则从事件活动 + 模块状态推导。"""
    if snapshot is None:
        return []
    if snapshot.roles:
        return normalize_roles(snapshot.roles)
    seen = []
    for ev in events:
        role = ev.role
        if role and role not in seen:
            seen.append(role)
    # 补上正在运行/已运行模块涉及的执行/审计角色
    for mid, st in snapshot.modules.items():
        if st in ("done", "running"):
            for role in ("executor", "auditor"):
                if role not in seen:
                    seen.append(role)
    # 稳定顺序
    return normalize_roles(seen)


def build_panel_state(snapshot, events, pending_path=None):
    """把快照 + 事件流拼成面板推送状态 dict。snapshot 可为 None（返回空状态骨架）。"""
    if events is None:
        events = []
    return {
        "stage": derive_stage(snapshot, events),
        "roles": derive_roles(snapshot, events),
        "consumption": build_consumption(snapshot, events),
        "pending": read_human_pending(snapshot, pending_path),
        "progress": build_progress(snapshot, events),
        "updated_at": snapshot.updated_at if snapshot is not None else None,
    }
