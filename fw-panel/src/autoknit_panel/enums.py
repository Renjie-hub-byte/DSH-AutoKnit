"""enums —— 跨模块共享枚举（与 contracts/data.yaml 的 shared_enums 对齐，禁止自定义）。

stage   : planning | exec | audit | split | idle
roles   : planner  | executor | auditor
choices : A | B | C | D | text
"""

STAGES = ("planning", "exec", "audit", "split", "idle")
ROLES = ("planner", "executor", "auditor")
HUMAN_CHOICES = ("A", "B", "C", "D", "text")

DEFAULT_STAGE = "idle"

# 事件类型 → 活跃角色 的粗粒度映射（供 stage/roles 推导）。
# 不在表内的进程级事件（scaffold/run.start/module.dispatch 等）不产生角色。
_EVENT_ROLE_KEYWORDS = (
    ("planner", "planner"),
    ("executor", "executor"),
    ("auditor", "auditor"),
)


def normalize_stage(value):
    """把任意输入规整为合法 stage 之一，非法值回退 DEFAULT_STAGE。"""
    if value in STAGES:
        return value
    if isinstance(value, str):
        lowered = value.lower()
        if lowered in STAGES:
            return lowered
    return DEFAULT_STAGE


def normalize_roles(value):
    """把任意输入规整为合法的 roles 列表（按 ROLES 顺序去重过滤）。"""
    if value is None:
        return []
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        return []
    seen = set()
    out = []
    for role in items:
        if not isinstance(role, str):
            continue
        r = role.lower()
        if r in ROLES and r not in seen:
            seen.add(r)
            out.append(r)
    return out


def role_for_event(event_type):
    """按事件类型名判断其归属角色；未知/进程级事件返回 None。"""
    if not event_type:
        return None
    lowered = event_type.lower()
    for keyword, role in _EVENT_ROLE_KEYWORDS:
        if keyword in lowered:
            return role
    return None
