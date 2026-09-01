"""真人决策提交/回复落盘模块。

真人（通过 DSH 面板）在待决策条目上选 A/B/C/D 或直接回文本，本模块把回复写入
``human_answer.json``（``human_answer_store``），格式对齐既有框架 human.py：
::

    {"choice": "A" | "B" | "C" | "D" | "text", "text": "可选的自由文本"}

写入成功后，既有框架 human.py / runner 会读取该文件 resume 接续，因此"触发
resume 接续"在 API 层即"把有效的 human_answer 落盘 + 确认可被读取"。本模块
提供 ``trigger_resume`` 显式确认文件已就绪。

路径解析优先级（对齐 ``data_contract``）：显式 ``path`` > 环境变量
``FW_HUMAN_ANSWER`` > ``task_root/总日志/human_answer.json``。

设计要点：
  * 自包含、纯标准库，不调任何 LLM。
  * 写入为原子的"临时文件 + rename"，避免半截 JSON 被 runner 读到。
  * 校验 choice 属于 ``A/B/C/D/text``；text 模式必须提供非空文本。
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Dict, Optional

from autoknit_panel.decision import HUMAN_CHOICES

# 默认存储相对路径（契约：总日志/human_answer.json）
DEFAULT_ANSWER_RELPATH = os.path.join("总日志", "human_answer.json")
ANSWER_ENV_VAR = "FW_HUMAN_ANSWER"


class AnswerError(ValueError):
    """human_answer 提交参数非法或写入失败。"""


# ---------------------------------------------------------------------------
# 路径解析
# ---------------------------------------------------------------------------
def resolve_answer_path(path: Optional[str] = None, task_root: Optional[str] = None) -> str:
    """解析 human_answer.json 的绝对/相对路径。

    优先级：显式 ``path`` > 环境变量 ``FW_HUMAN_ANSWER`` >
    ``task_root/总日志/human_answer.json``。task_root 缺省用 ``TASK_ROOT`` 环境变量。
    """
    if path:
        return os.path.abspath(path)
    env_path = os.environ.get(ANSWER_ENV_VAR)
    if env_path:
        return os.path.abspath(env_path)
    root = task_root or os.environ.get("TASK_ROOT")
    if root:
        return os.path.abspath(os.path.join(root, DEFAULT_ANSWER_RELPATH))
    return os.path.abspath(DEFAULT_ANSWER_RELPATH)


# ---------------------------------------------------------------------------
# choice 校验
# ---------------------------------------------------------------------------
_LETTER_CHOICES = ("A", "B", "C", "D")


def normalize_choice(choice: Any) -> str:
    """把任意输入规整为合法 human_choice；非法值抛 AnswerError。

    A/B/C/D 大小写不敏感；自由文本回复用 ``text``（保持契约枚举原样小写）。
    """
    raw = str(choice).strip()
    if raw.lower() == "text":
        return "text"
    value = raw.upper()
    if value not in _LETTER_CHOICES:
        raise AnswerError(
            f"非法 choice {choice!r}，合法值：{'/'.join(HUMAN_CHOICES)}"
        )
    return value


def validate_choice(choice: Any, text: Optional[str] = None) -> str:
    """校验 choice（含 text 模式是否带正文），返回规整后的 choice。"""
    normalized = normalize_choice(choice)
    if normalized == "text" and not (text or "").strip():
        raise AnswerError("choice=text 时必须提供非空的 text 正文")
    return normalized


# ---------------------------------------------------------------------------
# 落盘
# ---------------------------------------------------------------------------
def _atomic_write_json(path: str, record: Dict[str, Any]) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=os.path.basename(path) + ".", suffix=".tmp", dir=parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(record, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def write_answer(
    choice: Any,
    text: Optional[str] = None,
    path: Optional[str] = None,
    task_root: Optional[str] = None,
) -> Dict[str, Any]:
    """把真人决策写入 human_answer.json，返回落盘记录。

    Args:
        choice: A/B/C/D/text 之一。
        text: 自由文本；choice=text 时必填，其余可选。
        path: 显式路径（默认按契约解析）。
        task_root: 任务根目录，用于拼接默认路径。

    Returns:
        ``{"choice": ..., "text": ..., "path": ..., "resume_ready": True}``
    """
    normalized = validate_choice(choice, text)
    record: Dict[str, Any] = {"choice": normalized}
    if text is not None:
        record["text"] = text
    target = resolve_answer_path(path, task_root)
    _atomic_write_json(target, record)
    return {
        "choice": normalized,
        "text": record.get("text"),
        "path": target,
        "resume_ready": True,
    }


def submit_answer(
    choice: Any,
    text: Optional[str] = None,
    path: Optional[str] = None,
    task_root: Optional[str] = None,
) -> Dict[str, Any]:
    """提交/回复接口（语义别名）：同 ``write_answer``，面板提交决策/文本时调用。"""
    return write_answer(choice, text=text, path=path, task_root=task_root)


# ---------------------------------------------------------------------------
# 读取与 resume 确认
# ---------------------------------------------------------------------------
def read_answer(path: Optional[str] = None, task_root: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """读取 human_answer.json；文件不存在/非法返回 None。"""
    target = resolve_answer_path(path, task_root)
    if not os.path.exists(target):
        return None
    try:
        with open(target, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return None
        return data
    except (OSError, ValueError):
        return None


def trigger_resume(
    path: Optional[str] = None,
    task_root: Optional[str] = None,
    required_choice: Optional[str] = None,
) -> Dict[str, Any]:
    """确认 human_answer 已就绪、可被框架 resume 接续。

    Args:
        path: human_answer.json 路径（默认按契约解析）。
        task_root: 任务根目录。
        required_choice: 若给出，要求已落盘 choice 与之一致才算就绪。

    Returns:
        ``{"resumed": bool, "path": ..., "reason": ...}`` —— 文件存在且有效时为 True。
    """
    target = resolve_answer_path(path, task_root)
    answer = read_answer(target)
    if answer is None:
        return {"resumed": False, "path": target, "reason": "human_answer.json 不存在或非法"}
    choice = answer.get("choice")
    if required_choice is not None and choice != normalize_choice(required_choice):
        return {
            "resumed": False,
            "path": target,
            "reason": f"choice 不匹配（期望 {required_choice}，实际 {choice}）",
        }
    return {"resumed": True, "path": target, "reason": "answer 就绪，可 resume 接续"}
