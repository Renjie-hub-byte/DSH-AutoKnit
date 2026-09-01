"""回复通道文件写入（核心）。

将人工回复指令写入任务目录内的回复通道文件 ``needs_human/reply.md``。

文件格式（resume 侧可直接读取）：
    <command>          # 首行：指令 continue/retry/revise/自定义 或自定义指令
    <instruction>...   # 后续非空行：自由说明 / 自定义描述

写入是原子的（临时文件 + ``os.replace``）：任何失败都不会留下半成品 ``reply.md``。
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .errors import ReplyError, ReplyReason
from .status import NEEDS_HUMAN, read_status

# 通道目录名（在任务目录之下）与回复文件名。
CHANNEL_DIR_NAME = "needs_human"
CHANNEL_FILE_NAME = "reply.md"


@dataclass(frozen=True)
class WrittenReply:
    """一次成功写入的回复结果。"""

    task_id: str
    command: str
    written_to: str


def _build_content(command: str, instruction: str) -> str:
    """组装通道文件内容：首行指令 + 后续非空说明行。

    对空指令抛 ``写失败``（这属于内容不合法，与写盘失败同属确定性 error）。
    """
    command_line = command.strip()
    if not command_line:
        raise ReplyError(ReplyReason.WRITE_FAILED, "command 为空，无法写入回复通道")
    lines = [command_line]
    for line in instruction.splitlines():
        stripped = line.strip()
        if stripped:
            lines.append(stripped)
    return "\n".join(lines).rstrip() + "\n"


def _ensure_writable(channel_dir: Path) -> None:
    """通道目录必须已存在且可写，否则报 ``通道不可用``（前置校验，避免半成品）。"""
    if not channel_dir.is_dir():
        raise ReplyError(ReplyReason.CHANNEL_UNAVAILABLE, f"通道目录不存在: {channel_dir}")
    if not os.access(channel_dir, os.W_OK):
        raise ReplyError(ReplyReason.CHANNEL_UNAVAILABLE, f"通道目录不可写: {channel_dir}")


def write_reply(run_dir: str | Path, task_id: str, command: str,
                instruction: str = "") -> WrittenReply:
    """向目标任务写一条回复。

    流程（顺序确定性）：
      1. 任务目录必须存在，否则 ``通道不可用``；
      2. 状态必须为 ``needs_human``，否则 ``非needs_human``（防误操作）；
      3. 通道目录必须存在且可写，否则 ``通道不可用``；
      4. 原子写 ``needs_human/reply.md``，写盘异常映射为 ``写失败``，不留半成品。

    :param run_dir: 任务目录（内含 task.json 与 needs_human/ 通道目录）。
    :param task_id: 任务标识（透传回执）。
    :param command: 指令，应为 continue/retry/revise/自定义；内容将写入首行。
    :param instruction: 自由说明 / 自定义描述，写入首行之后的非空行。
    :return: 成功写入的 :class:`WrittenReply`。
    :raises ReplyError: 确定性失败（非needs_human/通道不可用/写失败）。
    """
    run = Path(run_dir)
    if not run.is_dir():
        raise ReplyError(ReplyReason.CHANNEL_UNAVAILABLE, f"任务目录不存在: {run}")

    status = read_status(run)
    if status != NEEDS_HUMAN:
        raise ReplyError(
            ReplyReason.NOT_NEEDS_HUMAN,
            f"任务 {task_id} 当前状态为 {status!r}，仅 needs_human 状态可回复",
        )

    channel_dir = run / CHANNEL_DIR_NAME
    _ensure_writable(channel_dir)

    target = channel_dir / CHANNEL_FILE_NAME
    content = _build_content(command, instruction)
    _atomic_write(target, content)
    return WrittenReply(task_id=task_id, command=command, written_to=str(target))


def _atomic_write(target: Path, content: str) -> None:
    """原子写文件：先写同目录临时文件再 ``os.replace`` 到目标。

    失败时清理临时文件并抛 ``写失败``；目标 ``reply.md`` 不会被写成半成品。
    """
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{CHANNEL_FILE_NAME}.", suffix=".tmp", dir=str(target.parent)
    )
    tmp = Path(tmp_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise ReplyError(ReplyReason.WRITE_FAILED, f"写入回复通道失败: {exc}") from exc
