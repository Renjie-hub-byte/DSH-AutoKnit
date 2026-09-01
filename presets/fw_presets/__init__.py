"""fw_presets —— 需求3 三角色 dsh preset 的机器契约辅助包（framework-v1/presets）。

职责范围（严格边界）：
- 只读校验 auditor 机器可解析判定（auditor-outcome.json + AUDIT_RESULT 四段行）：
  validate_outcome / parse_four_segment_line / build_four_segment_line /
  extract_four_segment_line；
- 只读加载 preset persona 文本（load_persona_text / iter_presets），供验收2 的
  铁律检查（tests/test_persona_rules.py）与 auditor 独立复现使用；
- 不执行任何写操作、不碰 dsh 核心、不改已审计模块源码。

与已审计框架产物的对齐点：
- fw-runner DriverOutcome 字段（verdict/root/confidence/blocker/reason/tokens/detail）
  —— 本包 schema 与之一致（见 protocol/auditor-outcome.schema.json）；
- fw-scaffold REVIEW.md 机器键（status/executor_round/auditor_round/root/confidence）
  —— auditor 只写内容小节，机器键由 runner 统一写回。
"""
from __future__ import annotations

VERSION = "1.0.0"

# 合法枚举（与 fw-runner model.py VERDICTS / ROOT_CAUSES 对齐）
VERDICTS = ("pass", "block")
ROOT_CAUSES = ("self", "upstream", "contract", "")
CONFIDENCE_LOW = 0.7  # confidence < 0.7 → 低置信（pro 重审/仲裁）

from .auditor_outcome import (  # noqa: E402
    build_four_segment_line,
    extract_four_segment_line,
    load_outcome,
    parse_four_segment_line,
    validate_outcome,
)
from .persona import get_persona_text, iter_presets, load_agent_cordis  # noqa: E402

__all__ = [
    "VERSION", "VERDICTS", "ROOT_CAUSES", "CONFIDENCE_LOW",
    "validate_outcome", "load_outcome", "parse_four_segment_line",
    "build_four_segment_line", "extract_four_segment_line",
    "iter_presets", "load_agent_cordis", "get_persona_text",
]
