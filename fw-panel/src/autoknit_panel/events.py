"""events —— 读取事件流 dispatch.jsonl 并规整为 Event 模型，支持角色活动推导。

事件行 shape：{"seq","ts","run_id","event","module","action","detail"}。
部分事件（scaffold）无 seq。非法/空行跳过。
"""

import json
import os
from dataclasses import dataclass, field

from .enums import role_for_event


@dataclass
class Event:
    seq: object = None
    ts: str = ""
    run_id: str = ""
    event: str = ""
    module: str = ""
    action: str = ""
    detail: dict = field(default_factory=dict)

    @property
    def role(self):
        """事件归属角色（planner/executor/auditor），进程级事件为 None。"""
        return role_for_event(self.event)

    @property
    def event_key(self):
        return self.event or ""


def parse_event_line(line):
    """解析单行事件 JSON；空行/非法行返回 None。"""
    if not line or not line.strip():
        return None
    try:
        data = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return Event(
        seq=data.get("seq"),
        ts=str(data.get("ts", "") or ""),
        run_id=str(data.get("run_id", "") or ""),
        event=str(data.get("event", "") or ""),
        module=str(data.get("module", "") or "") or None,
        action=str(data.get("action", "") or "") or None,
        detail=data.get("detail") if isinstance(data.get("detail"), dict) else {},
    )


def load_events(path):
    """从磁盘读取事件流文件；返回 Event 列表（文件不存在返回空列表）。"""
    if not os.path.isfile(path):
        return []
    events = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            ev = parse_event_line(line)
            if ev is not None:
                events.append(ev)
    return events


def events_for_module(events, module_id=None):
    """按 module 过滤事件；module_id 为 None 时返回全部（含进程级事件）。"""
    if module_id is None:
        return list(events)
    return [ev for ev in events if ev.module == module_id]


def roles_seen(events, module_id=None):
    """在事件流中出现的角色集合（按 ROLES 顺序）。"""
    seen = []
    for ev in events_for_module(events, module_id):
        role = ev.role
        if role and role not in seen:
            seen.append(role)
    return seen


def latest_role(events, module_id=None):
    """最近一个有角色归属的事件所对应的角色；无则 None。"""
    for ev in reversed(events_for_module(events, module_id)):
        role = ev.role
        if role:
            return role
    return None
