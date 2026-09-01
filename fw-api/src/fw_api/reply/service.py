"""dsh.task.reply —— 请示人工回复服务入口。

前端对 needs_human 任务提交回复指令（continue/retry/revise/自定义+说明），
本服务将其原子写入任务目录回复通道文件 ``needs_human/reply.md`` 供 resume 侧读取。

返回契约对齐的响应 item：
    ok    -> {status: ok,    task_id, command, written_to}
    error -> {status: error, task_id, reason,  detail?}
失败以 :class:`ReplyError` 抛出，由调用方渲染为 ``dsh.task.reply.error`` 回执。
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional, Union

from .channel import write_reply
from .effect import ChannelNotReady, read_channel
from .errors import ReplyError, ReplyReason
from .push import (
    CHANNEL_ERROR,
    CHANNEL_RESP,
    PushEvent,
    PushSink,
    build_error_item,
    build_resp_item,
)

# command 白名单，对齐契约枚举；'自定义' 需要后续 instruction 作为说明。
ALLOWED_COMMANDS = frozenset({"continue", "retry", "revise", "自定义"})

# 任务目录解析器：task_id -> run_dir 绝对路径；返回 None 表示无法解析。
TaskDirResolver = Callable[[str], Optional[Path]]


def _resolve_run_dir(
    task_id: str,
    resolver: Optional[TaskDirResolver],
    run_dir: Optional[Union[str, Path]],
) -> Path:
    """解析任务目录：显式 run_dir 优先，否则走 resolver；都无法解析时报 ``通道不可用``。"""
    if run_dir is not None:
        return Path(run_dir)
    if resolver is not None:
        resolved = resolver(task_id)
        if resolved is not None:
            return Path(resolved)
    raise ReplyError(
        ReplyReason.CHANNEL_UNAVAILABLE,
        f"无法解析任务 {task_id} 的目录（无 run_dir 且 resolver 未命中）",
    )


def submit_reply(
    *,
    task_id: str,
    command: str,
    instruction: str = "",
    run_dir: Optional[Union[str, Path]] = None,
    resolver: Optional[TaskDirResolver] = None,
) -> dict:
    """提交一条人工回复（dsh.task.reply, post）。

    :param task_id: 任务标识。
    :param command: 指令，∈ {continue, retry, revise, 自定义}。
    :param instruction: 自由说明 / 自定义描述。command 为 '自定义' 时作为核心说明写入。
    :param run_dir: 任务目录（测试/直连场景直接给路径）。
    :param resolver: 由 task_id 解析任务目录的回调（生产场景注入）。
    :return: ok 契约形状响应 item：
        {status: ok, task_id, command, written_to}
    :raises ReplyError: 确定性失败（非needs_human/通道不可用/写失败/参数不合法）。
    """
    if command not in ALLOWED_COMMANDS:
        raise ReplyError(
            ReplyReason.INVALID_COMMAND,
            f"command={command!r} 不在白名单 {sorted(ALLOWED_COMMANDS)}",
        )
    if command == "自定义" and not instruction.strip():
        raise ReplyError(
            ReplyReason.INVALID_COMMAND,
            "command='自定义' 时必须提供 instruction 说明",
        )

    run = _resolve_run_dir(task_id, resolver, run_dir)
    written = write_reply(run, task_id, command, instruction)
    return {
        "status": "ok",
        "task_id": written.task_id,
        "command": written.command,
        "written_to": written.written_to,
    }


def submit_reply_with_push(
    *,
    task_id: str,
    command: str,
    instruction: str = "",
    sink: PushSink,
    run_dir: Optional[Union[str, Path]] = None,
    resolver: Optional[TaskDirResolver] = None,
) -> dict:
    """提交回复并推送回执（dsh.task.reply + resp/error 联动入口）。

    成功：写通道 + 读回实际内容做生效确认，推 ``resp`` 回执后返回 ok item。
    失败：确定性推 ``error`` 回执后重抛 :class:`ReplyError`（不留半成品）。

    与仅调用 :func:`submit_reply` 的差异是：本入口同时把结果/回执推送到
    ``sink``（R→F push），并在成功路径读回通道内容作为 ``resp.content``。
    """
    run = _resolve_run_dir(task_id, resolver, run_dir)
    try:
        written = write_reply(run, task_id, command, instruction)
    except ReplyError as exc:
        sink.send(PushEvent(CHANNEL_ERROR, build_error_item(task_id, exc)))
        raise

    # 生效确认：回读通道实际内容（resume 侧可读的最终形态）作 resp.content。
    try:
        content = read_channel(run)
    except ChannelNotReady as exc:  # 写成功但回读失败：仍推 error，不留歧义
        err = ReplyError(ReplyReason.WRITE_FAILED, str(exc))
        sink.send(PushEvent(CHANNEL_ERROR, build_error_item(task_id, err)))
        raise err from exc

    sink.send(PushEvent(
        CHANNEL_RESP,
        build_resp_item(written.task_id, written.written_to, content),
    ))
    return {
        "status": "ok",
        "task_id": written.task_id,
        "command": written.command,
        "written_to": written.written_to,
    }
