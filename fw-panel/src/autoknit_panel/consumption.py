"""consumption —— 拼各角色 + 总 token 消耗（代码级，对齐契约 consumption 段）。

data_shape:
  consumption: { token_input:int, token_output:int, cache_hit:bool, duration_s:float }
  （extendable：另附 per_role 各角色明细，供面板展示）

策略：
  * 快照若显式给了 token_input/token_output → 直接采用；
  * 否则按 per_module.tokens_used 聚合，并按「模块归属角色」分摊到各角色；
  * 角色归属优先取事件流角色活动，其次按 per_module 的 executor_round/auditor_round；
  * duration_s：无显式值则取各模块 ended_at-started_at 之和。
"""

from .enums import ROLES


def _round_attr(detail, key):
    try:
        return int(detail.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _module_roles_from_events(events, module_id):
    roles = []
    for ev in events:
        if ev.module == module_id and ev.role and ev.role not in roles:
            roles.append(ev.role)
    roles.sort(key=lambda r: ROLES.index(r) if r in ROLES else 99)
    return roles


def _module_roles_from_detail(detail):
    roles = []
    if _round_attr(detail, "executor_round") > 0:
        roles.append("executor")
    if _round_attr(detail, "auditor_round") > 0:
        roles.append("auditor")
    return roles


def build_consumption(snapshot, events):
    """拼消耗结构；snapshot 为空返回全零骨架。"""
    zero = {
        "token_input": 0,
        "token_output": 0,
        "cache_hit": False,
        "duration_s": 0.0,
    }
    per_role = {role: dict(zero) for role in ROLES}
    if snapshot is None:
        return {**zero, "per_role": per_role}

    explicit_input = getattr(snapshot, "token_input", 0) or 0
    explicit_output = getattr(snapshot, "token_output", 0) or 0

    if explicit_input or explicit_output:
        # 契约显式给了 I/O 拆分 → 全局直接采用显式值；
        # 各角色按「模块 token 占比 / 该模块归属角色数」分摊，保证 per_role 总和 ≈ 显式值。
        total_module_tokens = sum(snapshot.module_tokens(mid) for mid in snapshot.module_ids) or 0
        cache_hit = bool(snapshot.cache_hit)
        for mid in snapshot.module_ids:
            roles = _roles_for(snapshot, events, mid)
            tokens = snapshot.module_tokens(mid)
            dur = snapshot.module_duration(mid)
            if not roles:
                continue
            in_share = explicit_input * tokens / total_module_tokens if total_module_tokens else 0
            out_share = explicit_output * tokens / total_module_tokens if total_module_tokens else 0
            per = in_share / len(roles)
            per_out = out_share / len(roles)
            for role in roles:
                per_role[role]["token_input"] += int(round(per))
                per_role[role]["token_output"] += int(round(per_out))
                per_role[role]["cache_hit"] = cache_hit
                if dur is not None:
                    per_role[role]["duration_s"] += dur
        token_input = explicit_input
        token_output = explicit_output
    else:
        # 无显式拆分 → 把 per_module.tokens_used 按归属角色分摊为 token_input。
        cache_hit = bool(snapshot.cache_hit)
        total_dur = 0.0
        for mid in snapshot.module_ids:
            roles = _roles_for(snapshot, events, mid)
            tokens = snapshot.module_tokens(mid)
            dur = snapshot.module_duration(mid)
            if dur is not None:
                total_dur += dur
            if roles and tokens:
                share = tokens / max(len(roles), 1)
                for role in roles:
                    per_role[role]["token_input"] += int(round(share))
                    per_role[role]["cache_hit"] = cache_hit
                    if dur is not None:
                        per_role[role]["duration_s"] += dur
        token_input = sum(per_role[r]["token_input"] for r in ROLES)
        token_output = 0

    duration_s = max(
        sum(per_role[r]["duration_s"] for r in ROLES),
        getattr(snapshot, "budget_used_tokens", 0) * 0 + 0.0,
    )

    return {
        "token_input": token_input,
        "token_output": token_output,
        "cache_hit": cache_hit,
        "duration_s": round(duration_s, 3),
        "per_role": per_role,
    }


def _roles_for(snapshot, events, module_id):
    roles = _module_roles_from_events(events, module_id)
    if roles:
        return roles
    detail = snapshot.per_module.get(module_id, {}) or {}
    roles = _module_roles_from_detail(detail)
    if roles:
        return roles
    # 未知归属：给 executor（最可能）；避免漏计。
    return ["executor"]
