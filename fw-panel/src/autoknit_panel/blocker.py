"""阻塞即停逻辑模块。

核心语义：**没回复不烧 token** —— 只要存在需要真人决策的待办，且还没有对应的
human_answer 落盘，面板/runner 就应停留在阻塞态，不再推进任何 LLM 会话。

本模块提供纯函数判断：

  * ``requires_human`` —— 单条 pending 是否要求真人。
  * ``is_blocked``     —— 综合待决策条目 + 已落盘 answer，判定当前是否应阻塞。
  * ``resume_ready``   —— 已落盘 answer 是否足以解除阻塞（可 resume）。

自包含、纯标准库；复用 ``decision.classify_pending`` 与 ``answer.read_answer``。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from autoknit_panel.decision import (
    PendingDecision,
    classify_pending,
)
from autoknit_panel.answer import read_answer

from autoknit_panel.decision import HUMAN_CHOICES


def requires_human(item: Any) -> bool:
    """判断单条 pending 条目是否必须等真人回复。

    * PendingDecision：看 ``needs_human``。
    * 其它（dict/字符串）：看 ``needs_human``/``wait_human`` 字段，缺省为 True。
    """
    if isinstance(item, PendingDecision):
        return item.needs_human
    if isinstance(item, dict):
        val = item.get("needs_human", item.get("wait_human", True))
        return bool(val)
    # 字符串/未知对象视为需要真人
    return True


def _answer_blocks_cleared(answer: Optional[Dict[str, Any]]) -> bool:
    """已落盘 answer 是否足以解除阻塞（choice 为合法 A/B/C/D/text 之一）。"""
    if not answer:
        return False
    choice = answer.get("choice")
    if choice is None:
        return False
    return str(choice).strip().upper() in HUMAN_CHOICES


def is_blocked(
    decisions: Optional[Iterable[Any]] = None,
    *,
    snapshot: Optional[Any] = None,
    events: Optional[Iterable[Any]] = None,
    human_pending: Optional[Iterable[Any]] = None,
    answer: Optional[Dict[str, Any]] = None,
    answer_path: Optional[str] = None,
    task_root: Optional[str] = None,
) -> bool:
    """判定当前是否处于"等待真人"阻塞态。

    逻辑：存在 ``needs_human`` 的待决策条目，且没有有效的已落盘 human_answer →
    阻塞（True）。没有待决策或已有有效 answer → 不阻塞（False）。

    Args:
        decisions: 已算好的待决策条目；不传则由本函数用 snapshot/events/human_pending 现算。
        answer: 已读出的 human_answer dict；不传则按 answer_path 读盘。
        answer_path/task_root: 读盘路径解析参数。
    """
    if decisions is None:
        decisions = classify_pending(snapshot=snapshot, events=events, human_pending=human_pending)
    decision_list = list(decisions)
    if not any(requires_human(d) for d in decision_list):
        return False
    if answer is None and (answer_path is not None or task_root is not None or snapshot is not None):
        answer = read_answer(answer_path, task_root)
    return not _answer_blocks_cleared(answer)


def resume_ready(
    decisions: Optional[Iterable[Any]] = None,
    *,
    snapshot: Optional[Any] = None,
    events: Optional[Iterable[Any]] = None,
    human_pending: Optional[Iterable[Any]] = None,
    answer: Optional[Dict[str, Any]] = None,
    answer_path: Optional[str] = None,
    task_root: Optional[str] = None,
) -> bool:
    """已落盘 answer 是否足以解除阻塞、可 resume 接续。与 ``is_blocked`` 互补。"""
    return not is_blocked(
        decisions,
        snapshot=snapshot,
        events=events,
        human_pending=human_pending,
        answer=answer,
        answer_path=answer_path,
        task_root=task_root,
    )
