"""确定性的 PRD markdown 解析器。

纯文本解析，不调任何 LLM、不联网。产出稳定可复现的结构，供 planner 做模块拆解。
选择 head 层级作为"模块候选"来源：一个 ``## `` 段落对应一个大模块，
其正文行数用于推导确定性行数估算。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ``# `` 标题 -> 任务名；``## `` 一级段落 -> 模块候选。
_H1_RE = re.compile(r"^\s*#\s+(.+?)\s*$")
_H2_RE = re.compile(r"^\s*##\s+(.+?)\s*$")


@dataclass
class Section:
    """一个 ``## `` 段落：名称 + 非空正文行（不含子标题）。"""

    name: str
    body_lines: list[str] = field(default_factory=list)


@dataclass
class ParsedPrd:
    title: str
    goal: str
    sections: list[Section]


def _clean_heading(text: str) -> str:
    # 去掉常见修饰符（code 围栏、markdown 链接），保留可读名。
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    return text.strip().strip("#").strip()


def parse_prd(text: str) -> ParsedPrd:
    """把 PRD 文本解析为结构化对象。空输入返回空 sections。"""
    title = ""
    goal = ""
    current: Section | None = None
    sections: list[Section] = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        h1 = _H1_RE.match(line)
        if h1:
            title = _clean_heading(h1.group(1))
            continue
        h2 = _H2_RE.match(line)
        if h2:
            current = Section(name=_clean_heading(h2.group(1)))
            sections.append(current)
            continue
        stripped = line.strip()
        if stripped:
            # 第一段有意义的正文作为 goal（任务名之下、首个模块之前）。
            if not goal and not sections:
                goal = stripped
            if current is not None:
                current.body_lines.append(stripped)

    if not title:
        title = "untitled-task"
    return ParsedPrd(title=title, goal=goal, sections=sections)
