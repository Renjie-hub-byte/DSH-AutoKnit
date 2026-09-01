"""m01 状态桥联动（status bridge）。

"状态桥"：上游（m01 状态管理）把任务状态落到任务目录 ``task.json{status}``，
本模块从该只读事实推导"是否可回复"。当任务进入 ``needs_human`` 状态即可回复；
回复写入后，通道文件内容的变化对下游（resume 侧 / 前端轮询）可见。

本模块不修改 fw-runner 核心逻辑，仅**观测**任务目录这一既成事实（边界）。
提供两个观测点：
    - :func:`can_reply`      —— 该任务当前是否处于可回复态；
    - :func:`observe_reply`  —— 观测某次回复前后通道文件是否变化（生效可见）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .channel import CHANNEL_DIR_NAME, CHANNEL_FILE_NAME
from .status import NEEDS_HUMAN, read_status

# task.json 之外的状态落盘读取函数（扩展留口：若 fw-runner 状态来源有差异，
# 仅需在此替换 status_reader）。
StatusReader = Callable[[Path], Optional[str]]


@dataclass(frozen=True)
class ReplyObservation:
    """一次回复观测：任务当时是否可回复，以及观测到的通道文件指纹。"""

    task_id: str
    replyable: bool
    status: Optional[str]
    channel_exists: bool
    channel_fingerprint: Optional[str]  # 通道文件内容指纹（不存在为 None）


def _fingerprint(path: Path) -> Optional[str]:
    """通道文件内容指纹（供比对"变化可见"）。"""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def observe(run_dir: str | Path, task_id: str,
            status_reader: StatusReader = read_status) -> ReplyObservation:
    """对某任务目录做一次状态/通道观测。"""
    run = Path(run_dir)
    status = status_reader(run)
    replyable = status == NEEDS_HUMAN
    ch = run / CHANNEL_DIR_NAME / CHANNEL_FILE_NAME
    exists = ch.is_file()
    return ReplyObservation(
        task_id=task_id,
        replyable=replyable,
        status=status,
        channel_exists=exists,
        channel_fingerprint=_fingerprint(ch) if exists else None,
    )


def can_reply(run_dir: str | Path, status_reader: StatusReader = read_status) -> bool:
    """任务当前是否处于 needs_human 可回复态（m01 状态桥判定）。"""
    return observe(run_dir, "?", status_reader=status_reader).replyable


def reply_changed(before: ReplyObservation, after: ReplyObservation) -> bool:
    """两次观测之间通道文件是否已发生可见变化。

    "变化可见"指：文件从不存在的 None → 出现，或内容指纹发生变化。
    """
    return (
        (before.channel_exists, before.channel_fingerprint)
        != (after.channel_exists, after.channel_fingerprint)
    )


def needs_human_appeared(run_dir: str | Path, task_id: str,
                         status_reader: StatusReader = read_status) -> bool:
    """观测当前任务是否已进入 needs_human（即"可回复的任务出现"）。"""
    obs = observe(run_dir, task_id, status_reader=status_reader)
    return obs.status == NEEDS_HUMAN
