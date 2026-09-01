"""回复生效确认（effect confirmation）。

"生效"定义为：回复通道文件已写入、内容格式正确（首行指令 + 后续非空说明）、
可被 fw-runner resume 侧读取。本模块提供对通道文件的只读校验与解析，
供 ``dsh.task.reply.resp`` 在推送前确认已生效（含回读实际内容）。

不修改 fw-runner 核心逻辑、不写 DSH 会话文件内部结构（边界）。
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from .channel import CHANNEL_DIR_NAME, CHANNEL_FILE_NAME

# 指令白名单（与 service.ALLOWED_COMMANDS 一致，便于独立校验）。
_ALLOWED = frozenset({"continue", "retry", "revise", "自定义"})


class ChannelNotReady(Exception):
    """通道文件尚不存在 / 不可读，无法完成生效确认。"""

    def __init__(self, path: Path) -> None:
        super().__init__(f"回复通道文件不可用: {path}")
        self.path = path


def channel_path(run_dir: str | Path) -> Path:
    """返回约定通道文件完整路径。"""
    return Path(run_dir) / CHANNEL_DIR_NAME / CHANNEL_FILE_NAME


def read_channel(run_dir: str | Path) -> str:
    """读回通道文件内容（resume 侧同款读取）。

    :raises ChannelNotReady: 文件不存在或不可读时。
    """
    path = channel_path(run_dir)
    if not path.is_file():
        raise ChannelNotReady(path)
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover —— 只读场景极难触发
        raise ChannelNotReady(path) from exc


def parse_reply(content: str) -> tuple[str, List[str]]:
    """把通道文件内容解析为 (command, description_lines)。

    首行为指令；其后的非空行为说明/自定义描述。
    """
    lines = [ln.strip() for ln in content.splitlines()]
    non_empty = [ln for ln in lines if ln]
    if not non_empty:
        return "", []
    return non_empty[0], non_empty[1:]


def confirm_effect(run_dir: str | Path, expected_command: str | None = None,
                   expected_instruction: str | None = None) -> dict:
    """校验回复是否已生效：文件存在、格式正确、可读，且与期望指令/说明一致。

    返回生效确认结果：
        {ok: bool, written_to: str, command: str, content: str, reasons: [str]}

    :param expected_command: 非 None 时要求首行指令 == expected_command。
    :param expected_instruction: 非 None 时要求其出现在后续说明中。
    """
    reasons: List[str] = []
    path = channel_path(run_dir)
    if not path.is_file():
        return {
            "ok": False,
            "written_to": str(path),
            "command": None,
            "content": None,
            "reasons": [f"通道文件不存在: {path}"],
        }

    try:
        content = read_channel(run_dir)
    except ChannelNotReady as exc:
        return {
            "ok": False,
            "written_to": str(path),
            "command": None,
            "content": None,
            "reasons": [str(exc)],
        }

    command, desc = parse_reply(content)
    if not command:
        reasons.append("通道文件为空，缺少指令首行")
    if expected_command is not None and command != expected_command:
        reasons.append(f"指令不符: 期望 {expected_command!r}，实际 {command!r}")

    if expected_instruction is not None:
        if not desc:
            reasons.append("缺少说明内容")
        elif expected_instruction not in "\n".join(desc):
            reasons.append(f"说明中未包含期望内容 {expected_instruction!r}")

    return {
        "ok": not reasons,
        "written_to": str(path),
        "command": command or None,
        "content": content,
        "reasons": reasons,
    }


def is_reply_format_valid(command: str, description_lines: List[str]) -> bool:
    """校验解析结果是否为合法回复格式：指令非空且在白名单。"""
    return bool(command) and command in _ALLOWED
