"""心跳守护（需求4）：连续 N 轮无实质产出 → 判静默卡死 → 进升级链。

- substance 判定 = 模块实质产出指纹变化（REVIEW.md 已做节 / status / src test 文件 /
  交付说明.md 的变化；logs/ tmp/ 豁免区不计）
- 判定逻辑薄封装，主循环在 runner.py（登记每轮 substance，累计 stall_count，
  达到 heartbeat_n_rounds 后按 block/self 路由）。
- call_with_deadline：墙钟超时守护（auditor 独立超时的兜底机制；子进程驱动另用
  ctx.timeout_seconds 让 subprocess 在 deadline 自行 kill，见 ScriptedAgentDriver）。
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Dict, Optional, Tuple, TypeVar

from .review import fingerprint

T = TypeVar("T")


def detect_stall(before_fp: str, after_fp: str) -> bool:
    """两轮指纹一致 → 本轮无实质产出（True = 卡死一轮）。"""
    return before_fp == after_fp


def should_escalate(stall_count: int, n_rounds: int) -> bool:
    """连续 stall_count 轮无产出 ≥ n_rounds → 触发升级。"""
    return stall_count >= n_rounds


def call_with_deadline(fn: Callable[[], T], seconds: Optional[float]) -> Tuple[Optional[T], bool]:
    """墙钟超时守护：在守护线程内执行 fn，seconds 秒内未返回 → 判超时。

    - 返回 (None, True)  = 超时触发（fn 仍在后台跑；进程内无法强杀线程，
      调用方应把死线同时传给子进程驱动让其自行 kill，见 AgentContext.timeout_seconds）
    - 返回 (result, False) = 正常完成（fn 抛出的异常原样上抛，含 RunInterrupted）
    - seconds 为空或 <= 0 → 不守护，当前线程直接执行（保持旧行为）
    """
    if not seconds or seconds <= 0:
        return fn(), False
    holder: Dict[str, Any] = {}

    def _run() -> None:
        try:
            holder["result"] = fn()
        except BaseException as exc:  # noqa: BLE001 —— 穿越线程边界原样上抛
            holder["exc"] = exc

    t = threading.Thread(target=_run, name="fw-call-with-deadline", daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        return None, True
    if "exc" in holder:
        raise holder["exc"]  # type: ignore[misc]
    return holder["result"], False
