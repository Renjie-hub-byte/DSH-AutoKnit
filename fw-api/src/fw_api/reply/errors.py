"""确定性错误类型与原因枚举。

原因枚举对齐契约 ``dsh.task.reply.error`` 的 ``reason`` 字段：
``[非needs_human, 通道不可用, 写失败]``。契约 marked ``extendable: true``，
故保留扩展位 ``INVALID_COMMAND`` 用于入参不合法（自定义指令缺失等）。
"""

from __future__ import annotations

from enum import Enum


class ReplyReason(str, Enum):
    """回复失败原因（机器可读），取值对齐 dsh.task.reply.error 契约。"""

    NOT_NEEDS_HUMAN = "非needs_human"
    CHANNEL_UNAVAILABLE = "通道不可用"
    WRITE_FAILED = "写失败"
    # 契约 extendable 扩展位：入参不合法（如 command 不在白名单）。
    INVALID_COMMAND = "参数不合法"


class ReplyError(Exception):
    """一个确定性的、面向用户的回复失败。

    携带机器可读的 ``reason``（:class:`ReplyReason`）与可选人类可读 ``detail``。
    上层可 catch 本异常并将其渲染为 ``dsh.task.reply.error`` 回执，保证失败路径
    同样确定、可测试、不留半成品。
    """

    def __init__(self, reason: ReplyReason, detail: str | None = None) -> None:
        super().__init__(f"{reason.value}: {detail}" if detail else reason.value)
        self.reason = reason
        self.detail = detail

    def to_dict(self, task_id: str) -> dict:
        """渲染为 dsh.task.reply.error 契约形状的响应 item。"""
        item = {
            "status": "error",
            "task_id": task_id,
            "reason": self.reason.value,
        }
        if self.detail is not None:
            item["detail"] = self.detail
        return item
