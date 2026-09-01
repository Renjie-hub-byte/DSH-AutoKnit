"""snapshot —— 读取 总日志/快照.json（snapshot_store）并规整为 Snapshot 模型。

需兼容两种 shape：
  * 既有 runner 写的 shape：schema_version / run_id / task / status / cause / note /
    modules{id:status} / dependencies / failure_counts / per_module{id:detail} /
    needs_human / completed_order / budget_used_tokens / last_seq；
  * 数据契约描述的 shape：stage / roles / token_input / token_output / cache_hit / pending。

两者可叠加（eg. 契约字段缺失时从 runner 字段推导），本模块只读不写。
"""

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .enums import STAGES, ROLES, normalize_stage, normalize_roles

# module 状态集合（runner 语义）
_MODULE_DONE = "done"
_MODULE_TERMINAL = frozenset({"done"})


def _as_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_ts(value):
    """解析 ISO 时间戳；失败返回 None。"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _duration_between(start, end):
    start = _parse_ts(start)
    end = _parse_ts(end)
    if start is None or end is None:
        return None
    delta = (end - start).total_seconds()
    return max(0.0, delta)


@dataclass
class Snapshot:
    """规整后的运行状态快照（只读模型）。"""

    run_id: str = ""
    task: str = ""
    status: str = "unknown"
    updated_at: str = ""
    note: str = ""
    schema_version: int = 0

    # 契约字段（可能为空 → 需 derive）
    stage: str = ""
    roles: list = field(default_factory=list)
    cache_hit: bool = False

    # runner 字段
    modules: dict = field(default_factory=dict)          # module_id -> status
    dependencies: dict = field(default_factory=dict)     # module_id -> [dep ids]
    failure_counts: dict = field(default_factory=dict)
    per_module: dict = field(default_factory=dict)       # module_id -> detail dict
    needs_human: list = field(default_factory=list)
    completed_order: list = field(default_factory=list)
    budget_used_tokens: int = 0

    # 聚合消耗（存在则优先用）
    token_input: int = 0
    token_output: int = 0

    raw: dict = field(default_factory=dict)

    # ---- 便捷派生 ----
    @property
    def all_modules_done(self):
        return bool(self.modules) and all(
            v in _MODULE_TERMINAL for v in self.modules.values()
        )

    @property
    def module_ids(self):
        return list(self.modules.keys())

    @property
    def done_module_ids(self):
        return [mid for mid, st in self.modules.items() if st in _MODULE_TERMINAL]

    def module_tokens(self, module_id):
        """单个模块的 token 消耗（per_module detail 的 tokens_used 或字段）。"""
        detail = self.per_module.get(module_id, {})
        if not detail:
            return 0
        return _as_int(detail.get("tokens_used", 0))

    def module_duration(self, module_id):
        """单个模块耗时秒数（ended_at - started_at，缺失返回 None）。"""
        detail = self.per_module.get(module_id, {})
        if not detail:
            return None
        return _duration_between(detail.get("started_at"), detail.get("ended_at"))


def parse_snapshot(data):
    """把快照 dict 规整为 Snapshot（兼容 runner 与契约两种 shape）。"""
    data = data or {}
    snap = Snapshot(raw=data)

    snap.run_id = str(data.get("run_id", "") or "")
    snap.task = str(data.get("task", "") or "")
    snap.status = str(data.get("status", "unknown") or "unknown")
    snap.updated_at = str(data.get("updated_at", "") or "")
    snap.note = str(data.get("note", "") or "")
    snap.schema_version = _as_int(data.get("schema_version", 0))

    # 契约字段：仅当快照显式给出时才视为显式值；否则留空交由 derive 推导。
    if data.get("stage") is not None:
        snap.stage = normalize_stage(data["stage"])
    else:
        snap.stage = ""
    snap.roles = normalize_roles(data.get("roles"))
    snap.cache_hit = bool(data.get("cache_hit", False))
    snap.token_input = _as_int(data.get("token_input", 0))
    snap.token_output = _as_int(data.get("token_output", 0))

    # runner 字段
    snap.modules = dict(data.get("modules", {}) or {})
    snap.dependencies = dict(data.get("dependencies", {}) or {})
    snap.failure_counts = dict(data.get("failure_counts", {}) or {})
    snap.per_module = dict(data.get("per_module", {}) or {})
    snap.needs_human = list(data.get("needs_human", []) or [])
    snap.completed_order = list(data.get("completed_order", []) or [])
    snap.budget_used_tokens = _as_int(data.get("budget_used_tokens", 0))

    # 若契约给了 stage/roles，则正常；否则保持空，交由 state.derive_stage 推导。
    return snap


def load_snapshot(path):
    """从磁盘读取快照 JSON 文件。文件不存在/非法 → 抛出 SnapshotReadError。"""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"snapshot 不存在: {path}")
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"snapshot 应为 JSON 对象，得到 {type(data).__name__}: {path}")
    return parse_snapshot(data)


class SnapshotReadError(Exception):
    """快照读取/解析失败。"""
