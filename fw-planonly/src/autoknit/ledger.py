"""事件日志 + token 账本。

plan-only 的关键约束是"只跑 planner、不产生 executor/auditor/split 事件、不发 LLM 请求"。
:class:`Ledger` 用 append-only JSON Lines 记录每个事件（角色/阶段/种类），并在收尾写一份
token 账本汇总；它内建"plan-only 允许集合"，一旦试图写入 executor/auditor 角色或
exec/audit/split 阶段即抛错——从数据上保证本模式绝不越轨。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .errors import AutoknitError

# plan-only 允许的角色与阶段（对齐共享枚举 shared_enums）。
ALLOWED_ROLES = {"planner"}
ALLOWED_STAGES = {"planning", "idle"}


class LedgerViolationError(AutoknitError):
    """试图记录 plan-only 不允许的角色/阶段事件。"""

    exit_code = 5


class Ledger:
    def __init__(self, events_path: str | Path, tokens_path: str | Path, token_input: int = 0, token_output: int = 0) -> None:
        self.events_path = Path(events_path)
        self.tokens_path = Path(tokens_path)
        self._seq = 0
        self._events: list[dict[str, Any]] = []
        self.token_input = token_input
        self.token_output = token_output
        self.cache_hit = "0"

    def record(self, role: str, stage: str, kind: str, msg: str = "") -> None:
        """记录一条事件。校验 role/stage 是否属于 plan-only 允许集合。"""
        if role not in ALLOWED_ROLES:
            raise LedgerViolationError(
                f"plan-only 模式禁止记录角色 {role!r}（只允许 {sorted(ALLOWED_ROLES)}）"
            )
        if stage not in ALLOWED_STAGES:
            raise LedgerViolationError(
                f"plan-only 模式禁止记录阶段 {stage!r}（只允许 {sorted(ALLOWED_STAGES)}）"
            )
        self._seq += 1
        self._events.append(
            {"seq": self._seq, "role": role, "stage": stage, "kind": kind, "msg": msg}
        )

    @property
    def events(self) -> list[dict[str, Any]]:
        return list(self._events)

    def write(self, cache_hit: str | None = None) -> None:
        """把事件落盘为 JSON Lines，并写出 token 账本汇总。"""
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as fh:
            for event in self._events:
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        if cache_hit is not None:
            self.cache_hit = cache_hit
        summary = {
            "token_input": self.token_input,
            "token_output": self.token_output,
            "cache_hit": self.cache_hit,
            "llm_requests": 0,  # plan-only 不发起任何 LLM 请求
            "roles_seen": sorted({e["role"] for e in self._events}),
            "stages_seen": sorted({e["stage"] for e in self._events}),
            "forbidden_events": 0,
        }
        self.tokens_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def read_ledger(events_path: str | Path) -> list[dict[str, Any]]:
    """读取已落盘的事件（供验收核对）。文件不存在返回空列表。"""
    path = Path(events_path)
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            events.append(json.loads(line))
    return events
