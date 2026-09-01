# -*- coding: utf-8 -*-
"""m03 —— 消耗汇总服务 dsh.usage.summary

基于 m01 的 DSH 会话解析能力（dsh_parser），实现消耗汇总拉取接口：
按 会话 / 任务 标识维度聚合消耗 token 量，返回汇总对象；与 fw-token 实测值对拍一致。

接口契约（contract.yaml read_api）：
  path: dsh.usage.summary
  method: ["get"]    # F→R 拉取 DSH 会话 / 任务消耗汇总

入参只需 会话 / 任务 标识 维度（identifiers），不含字段定义。

数据源：
  1) fw-token 输出文本（fw-token 实测口径）：summarize_fw_token_output
  2) DSH 会话文件（.jsonl / .jsonl.zstd）：summarize_session_file
  3) DSH 会话目录：summarize_session_dir

边界对齐（任务书 boundaries）：
  - 只读消费：不修改 / 重写 / 修复 DSH 会话文件本体（复用 m01 解析能力）。
  - 不涉及 token 记账 / 扣费：本模块只做聚合汇总，不做账务。
  - 不实现文件变更事件（归 m04）。
  - 不生成任何页面 / 可视化输出。

设计约束（对齐任务书验收）：
  - 无数据时返回确定性空汇总（全 0），不崩、不伪造。
  - 解析异常由 m01 抛 SessionParseError；本模块据此兜底为空汇总 / 错误对象。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Optional, Sequence

# 收敛：上游会话解析器 dsh_parser 已并入本包（fw_api/parser.py），不再硬编码外部路径。
from .parser import (  # noqa: E402
    SessionParseError,
    parse_fw_token_output,
    parse_session_dir,
    parse_session_file,
)


class UsageSummaryError(Exception):
    """消耗汇总拉取失败（确定性错误；空数据不应触发，应返回空汇总）。"""


def _empty_summary_dict() -> dict:
    """确定性空汇总：全 0，无会话明细。"""
    return {
        "sessions": 0,
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "billed_tokens": 0,
        "cache_hit_tokens": 0,
        "cache_hit_ratio": 0.0,
        "per_session": {},
    }


@dataclass
class UsageSummary:
    """按 会话 / 任务 聚合后的消耗汇总。

    字段与 fw-token 对拍口径一致：input/output/billed/cache/calls/sessions。
    """
    sessions: int = 0
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    billed_tokens: int = 0          # 计费 token = input + output（与 fw-token 计费口径一致）
    cache_hit_tokens: int = 0
    cache_hit_ratio: float = 0.0
    per_session: dict = field(default_factory=dict)   # {会话/任务标识: 明细 dict}

    def to_dict(self) -> dict:
        return {
            "sessions": self.sessions,
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "billed_tokens": self.billed_tokens,
            "cache_hit_tokens": self.cache_hit_tokens,
            "cache_hit_ratio": round(self.cache_hit_ratio, 4),
            "per_session": self.per_session,
        }


# ---------------------------------------------------------------------------
# 内部聚合工具
# ---------------------------------------------------------------------------

def _merge_detail(total: UsageSummary, ident: str, detail: dict) -> None:
    """把单个会话/任务的明细合并进 total。detail 字段名与 fw-token 行一致。"""
    i = int(detail.get("input", detail.get("input_tokens", 0)) or 0)
    o = int(detail.get("output", detail.get("output_tokens", 0)) or 0)
    c = int(detail.get("cache", detail.get("cache_hit_tokens", 0)) or 0)
    n = int(detail.get("calls", detail.get("n", 0)) or 0)
    total.sessions += 1
    total.calls += n
    total.input_tokens += i
    total.output_tokens += o
    total.cache_hit_tokens += c
    total.per_session[ident] = {
        "input_tokens": i,
        "output_tokens": o,
        "billed_tokens": i + o,
        "cache_hit_tokens": c,
        "calls": n,
    }
    total.billed_tokens = total.input_tokens + total.output_tokens
    if (total.input_tokens + total.cache_hit_tokens) > 0:
        total.cache_hit_ratio = total.cache_hit_tokens / (
            total.input_tokens + total.cache_hit_tokens
        )


def _select_identifiers(all_ids: Sequence[str], identifiers) -> list:
    """按入参 identifiers 维度过滤；None / ['*'] / ['all'] 表示全部。

    identifiers 中不存在的标识被忽略（不报错，符合"会话不存在返回确定性空"的只读语义）。
    """
    ids = list(all_ids)
    if identifiers is None:
        return ids
    if isinstance(identifiers, str):
        identifiers = [identifiers]
    if any(i in ("*", "all") for i in identifiers):
        return ids
    return [i for i in identifiers if i in ids]


# ---------------------------------------------------------------------------
# 三种数据源的汇总
# ---------------------------------------------------------------------------

def summarize_fw_token_output(text: str, identifiers=None) -> UsageSummary:
    """从 fw-token 输出文本聚合消耗汇总，与 fw-token 实测值对拍一致。

    无数据 / 无法解析时返回确定性空汇总（不崩、不伪造）。
    """
    try:
        parsed = parse_fw_token_output(text)
    except SessionParseError:
        return UsageSummary()
    rows = {r["session"]: r for r in parsed["rows"]}
    selected = _select_identifiers(list(rows.keys()), identifiers)
    total = UsageSummary()
    for ident in selected:
        _merge_detail(total, ident, rows[ident])
    if parsed["calls"] == 0 and not total.per_session:
        # fw-token 有合计但无行（如仅合计无明细）：直接采用合计口径
        total.sessions = int(parsed["sessions"])
        total.calls = int(parsed["calls"])
        total.input_tokens = int(parsed["input_tokens"])
        total.output_tokens = int(parsed["output_tokens"])
        total.cache_hit_tokens = int(parsed["cache_hit_tokens"])
        total.billed_tokens = int(parsed["billed_tokens"])
        total.cache_hit_ratio = float(parsed["cache_hit_ratio"])
    return total


def summarize_session_file(path: str, identifiers=None) -> UsageSummary:
    """汇总单个 DSH 会话文件（.jsonl / .jsonl.zstd）的消耗。只读。"""
    st = parse_session_file(path).to_dict()
    ident = os.path.basename(path)
    detail = {
        "input_tokens": st["input_tokens"],
        "output_tokens": st["output_tokens"],
        "cache_hit_tokens": st["cache_hit_tokens"],
        "calls": st["calls"],
    }
    total = UsageSummary()
    if not st["calls"] and not st["input_tokens"] and not st["output_tokens"]:
        # 文件无 token 上报：返回确定性空汇总（有会话但零消耗）
        return total
    _merge_detail(total, ident, detail)
    return total


def summarize_session_dir(
    path: str, *, pattern: str = "*.jsonl.zstd", identifiers=None
) -> UsageSummary:
    """汇总一个 DSH 会话目录下所有会话文件的消耗，按会话聚合。只读。

    目录不存在时抛 UsageSummaryError；单个文件损坏时以 error 占位不影响其余。
    """
    if not os.path.isdir(path):
        raise UsageSummaryError(f"not a directory: {path}")
    parsed = parse_session_dir(path, pattern=pattern)
    # 去掉损坏占位 {error:...}，只聚合成功解析的会话
    valid = {k: v for k, v in parsed.items() if isinstance(v, dict) and "error" not in v}
    selected = _select_identifiers(list(valid.keys()), identifiers)
    total = UsageSummary()
    for ident in selected:
        _merge_detail(total, ident, valid[ident])
    return total


def dsh_usage_summary(source, identifiers=None) -> UsageSummary:
    """统一拉取入口（dsh.usage.summary get）。

    source 类型推断数据源：
      - 目录路径 (os.path.isdir) → summarize_session_dir
      - 文件路径 (os.path.isfile) → summarize_session_file
      - 其它（视为 fw-token 输出文本）→ summarize_fw_token_output

    返回 UsageSummary.to_dict() 兼容对象；无数据时返回确定性空汇总。
    """
    if isinstance(source, str) and os.path.isdir(source):
        pattern = "*.jsonl.zstd"
        if not any(f.endswith(".jsonl.zstd") for f in os.listdir(source)):
            pattern = "*.jsonl" if any(f.endswith(".jsonl") for f in os.listdir(source)) else "*.jsonl.zstd"
        return summarize_session_dir(source, pattern=pattern, identifiers=identifiers)
    if isinstance(source, str) and os.path.isfile(source):
        return summarize_session_file(source, identifiers=identifiers)
    return summarize_fw_token_output(source, identifiers=identifiers)


__all__ = [
    "UsageSummary",
    "UsageSummaryError",
    "summarize_fw_token_output",
    "summarize_session_file",
    "summarize_session_dir",
    "dsh_usage_summary",
]
