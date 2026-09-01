"""验收冲突检测（"快 vs 安全"类关键词）。

设计意图：验收标准若同时带有"求快/性能优先"与"求稳/安全第一"倾向，二者优先级需要
真人拍板 —— 本模块只负责【标记冲突】，绝不代为定优先级（三权分立 / 人工拍板原则）。

输出为 severity="conflict" 的 Issue：
- 不算 error（任务书结构合法）；但 status 变为 "conflict"，CLI 退出码 2
  （与 "pass"=0 / "error"=1 区分，供编排层回人定优先级）。
- 可通过 integration.check.acceptance_conflict=false 关闭。

关键词命中是启发式的（可能误报/漏报）。误报只会多一次人工确认，方向安全；
漏报留给 auditor 在验收阶段人工兜底。
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence

from .model import Issue

# 默认冲突关键词组："快/性能" vs "安全/稳定"。可整体替换（见 validate 的 groups 参数）。
DEFAULT_CONFLICT_GROUPS: Dict[str, List[str]] = {
    "speed": [
        "快", "性能", "提速", "低延迟", "低时延", "响应快", "尽快", "赶进度",
        "先上线", "越快越好", "speed", "fast", "quick", "performance", "latency",
    ],
    "safety": [
        "安全", "稳定", "可靠", "万无一失", "不出错", "零出错", "严谨",
        "保守", "慎重", "宁慢勿错", "质量优先", "安全第一", "safety",
        "secure", "stable", "reliable", "robust", "correctness",
    ],
}


def _match_keywords(text: str, keywords: Sequence[str]) -> List[str]:
    lowered = text.lower()
    return [k for k in keywords if k.lower() in lowered]


def check_module_conflict(
    module: Mapping[str, Any],
    groups: Mapping[str, Sequence[str]] | None = None,
) -> List[Issue]:
    """对单个模块：objective + acceptance + boundaries 文本里同时命中两类关键词 → conflict。"""
    g = groups if groups is not None else DEFAULT_CONFLICT_GROUPS
    mid = module.get("id")
    parts = [
        str(module.get("name") or ""),
        str(module.get("objective") or ""),
        " ".join(str(x) for x in (module.get("acceptance") or [])),
        " ".join(str(x) for x in (module.get("boundaries") or [])),
    ]
    text = "\n".join(parts)
    matched: Dict[str, List[str]] = {}
    for group_name, keywords in g.items():
        hits = _match_keywords(text, keywords)
        if hits:
            matched[group_name] = hits
    if len(matched) >= 2:
        return [Issue(
            code="acceptance_conflict",
            severity="conflict",
            message=(
                f"模块 {mid} 的验收同时包含「{'/'.join(sorted(matched))}」两类倾向"
                f"（关键词：{matched}），优先级需人工定夺"
            ),
            module_id=mid,
            detail={"groups": {k: sorted(v) for k, v in matched.items()}},
        )]
    return []


def validate_acceptance_conflicts(
    modules: Sequence[Mapping[str, Any]],
    groups: Mapping[str, Sequence[str]] | None = None,
) -> List[Issue]:
    """对全部模块跑验收冲突检测。"""
    issues: List[Issue] = []
    for m in modules:
        issues.extend(check_module_conflict(m, groups))
    return issues
