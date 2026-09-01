"""R→F 推送回执（dsh.task.reply.resp / dsh.task.reply.error）。

纯代码环境下没有真实传输层，本模块承担两件事：
  1. 回执 item 构造器 —— 产出契约对齐的 ``resp`` / ``error`` item；
  2. 推送出口抽象 :class:`PushSink` 与进程内实现 :class:`InProcessPushSink`，
     让调用方在回复写入成功/失败后把回执"推送"出去。真实 WebSocket/SSE 传输层
     只需实现 :class:`PushSink` 接入，不改回执构造逻辑。

回执契约（contract.yaml）：
    resp  : item = {status: ok, task_id, written_to, content}          （extendable）
    error : item = {status: error, task_id, reason, detail?}           （extendable）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .errors import ReplyError

# 推送通道名（对应接口 path 的后缀）。
CHANNEL_RESP = "resp"
CHANNEL_ERROR = "error"


def build_resp_item(task_id: str, written_to: str, content: str) -> dict:
    """构造 dsh.task.reply.resp 的成功回执 item。

    :param content: 通道文件的实际内容（供前端 / resume 侧确认已生效）。
    """
    return {
        "status": "ok",
        "task_id": task_id,
        "written_to": written_to,
        "content": content,
    }


def build_error_item(task_id: str, error: ReplyError) -> dict:
    """构造 dsh.task.reply.error 的失败回执 item。

    ``reason`` 取 :class:`ReplyError.reason`（∈[非needs_human, 通道不可用, 写失败,
    参数不合法]），与契约对齐；``detail`` 仅在存在时输出。
    """
    item: dict = {
        "status": "error",
        "task_id": task_id,
        "reason": error.reason.value,
    }
    if error.detail is not None:
        item["detail"] = error.detail
    return item


@dataclass(frozen=True)
class PushEvent:
    """一条已产生的推送事件。"""

    channel: str          # CHANNEL_RESP | CHANNEL_ERROR
    item: dict            # 契约对齐的回执 item


class PushSink:
    """推送出口抽象：向前端/下游推送一条回执事件。

    真实环境应继承并实现 :meth:`send`（接 WebSocket/SSE 等传输）。
    """

    def send(self, event: PushEvent) -> None:
        raise NotImplementedError  # pragma: no cover


class InProcessPushSink(PushSink):
    """进程内推送出口：把事件收集到内存列表，供测试断言与进程内事件桥使用。"""

    def __init__(self) -> None:
        self._events: List[PushEvent] = []

    def send(self, event: PushEvent) -> None:
        self._events.append(event)

    @property
    def events(self) -> List[PushEvent]:
        return list(self._events)

    def of_channel(self, channel: str) -> List[PushEvent]:
        """返回指定通道的事件（按推送先后序）。"""
        return [e for e in self._events if e.channel == channel]

    def last(self) -> Optional[PushEvent]:
        return self._events[-1] if self._events else None

    def clear(self) -> None:
        self._events.clear()
