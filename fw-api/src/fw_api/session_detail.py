# -*- coding: utf-8 -*-
"""m02 —— 会话详情服务 dsh.session.detail（F→R 拉取接口）

基于 m01（dsh_parser）的解析能力，对外暴露 `dsh.session.detail` 拉取接口：
按会话标识(session_id)返回该会话的详情字段，字段值与 m01 解析结果一致。

接口入参：
  - get(session_id)：仅需会话标识，不含任何会话内字段。

返回字段（来自 m01 SessionStats）：
  rounds / steps / input_tokens / output_tokens / token_speed /
  cache_hit_tokens / cache_hit_ratio / calls / duration_ms

会话不存在 / 解析失败时的兜底（确定性，进程不崩）：
  - get(session_id)：缺失抛 SessionNotFoundError（明确错误信息）；
  - get_or_empty(session_id)：缺失返回确定性空 dict {}。

设计约束：
  - 只读消费：不解析修复 / 重写 / 不修改会话文件本体（由 m01 保证）。
  - 不产出消耗汇总（归 m03）、不做任何 token 记账 / 扣费。
  - 依赖 m01：通过 m01.dsh_parser.parse_session_dir 构建「会话标识 -> 详情」索引。
"""

from __future__ import annotations

import os
import sys

__all__ = ["SessionNotFoundError", "SessionDetailService", "get_session_detail"]


class SessionNotFoundError(Exception):
    """会话不存在或无法解析（确定性错误，供调用方兜底；进程不崩）。"""


def _load_m01_parser():
    """加载会话解析器（收敛：dsh_parser 已并入本包 fw_api/parser.py）。

    不再硬编码外部 modules/ 路径——统一从 fw_api.parser 导入。
    """
    from . import parser

    return parser


class SessionDetailService:
    """会话详情拉取服务。构造时用 m01 解析数据目录构建索引。"""

    def __init__(self, data_dir: str, *, pattern: str = "*.jsonl.zstd"):
        """data_dir：DSH 会话数据目录；pattern：会话文件匹配模式。"""
        if not data_dir:
            raise ValueError("data_dir is required")
        self._parser = _load_m01_parser()
        self.data_dir = data_dir
        self.pattern = pattern
        # 会话标识 -> 详情 dict（缺失文件以 {"error": ...} 占位，由 get 兜底）
        self._index = self._parser.parse_session_dir(data_dir, pattern=pattern)

    def exists(self, session_id: str) -> bool:
        return session_id in self._index

    def get(self, session_id: str) -> dict:
        """返回指定会话的详情字段。

        会话不存在 / 解析失败时抛 SessionNotFoundError（明确错误），进程不崩。
        """
        if session_id not in self._index:
            raise SessionNotFoundError(f"session not found: {session_id}")
        entry = self._index[session_id]
        if isinstance(entry, dict) and "error" in entry:
            raise SessionNotFoundError(
                f"session parse failed: {session_id}: {entry['error']}"
            )
        return dict(entry)

    def get_or_empty(self, session_id: str) -> dict:
        """返回指定会话详情；缺失时返回确定性空 dict {}（不抛错、不崩）。"""
        try:
            return self.get(session_id)
        except SessionNotFoundError:
            return {}

    # ---- 与 m01 解析结果一致性：将索引暴露给测试 / 下游对拍 ----
    def index(self) -> dict:
        """返回 {会话标识: 详情} 索引（只读视图，与 m01 解析结果一致）。"""
        return dict(self._index)


# ---------------------------------------------------------------------------
# 便捷函数式入口（等价于上面的类接口）
# ---------------------------------------------------------------------------

def get_session_detail(data_dir: str, session_id: str, *, pattern: str = "*.jsonl.zstd") -> dict:
    """一次性拉取：返回详情；缺失抛 SessionNotFoundError。"""
    return SessionDetailService(data_dir, pattern=pattern).get(session_id)
