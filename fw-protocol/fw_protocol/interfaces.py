"""接口重复检测。

协议铁律：接口只到「路径前缀 + 方法」级（禁止规划期定字段）。
重复判定 = 路径前缀完全相同 且 方法集合有交集（方法大小写归一为大写）。
命中即在两个模块之间（或同一模块内）报 error，指出双方模块 id、冲突路径与共享方法。

注意：通配符前缀的"语义覆盖"（如 /api/order/* 覆盖 /api/order/item）暂不做重叠检测，
只做精确前缀重复——这是明确的范围限制（见 docs/schema.md 已知限制）。
"""
from __future__ import annotations

import dataclasses
from typing import Any, Dict, List, Sequence, Set, Tuple

from .model import Issue


def normalize_method(method: Any) -> Set[str]:
    """方法归一化：字符串 → {大写}；列表 → {大写...}。空/非法输入 → 空集。"""
    if isinstance(method, str):
        return {method.strip().upper()} if method.strip() else set()
    if isinstance(method, list):
        out: Set[str] = set()
        for m in method:
            if isinstance(m, str) and m.strip():
                out.add(m.strip().upper())
        return out
    return set()


def extract_interfaces(modules: Sequence[Dict[str, Any]]) -> List[Tuple[str, str, Set[str]]]:
    """模块列表 → [(module_id, path, methods_set), ...]。"""
    out: List[Tuple[str, str, Set[str]]] = []
    for m in modules:
        mid = m.get("id")
        if not isinstance(mid, str):
            continue
        for iface in m.get("interfaces") or []:
            if isinstance(iface, dict):
                path = iface.get("path")
                if isinstance(path, str) and path.strip():
                    out.append((mid, path.strip(), normalize_method(iface.get("method"))))
    return out


def find_interface_duplicates(modules: Sequence[Dict[str, Any]]) -> List[Issue]:
    """同 前缀 + 方法（交集非空）→ error，指出双方模块。"""
    entries = extract_interfaces(modules)
    issues: List[Issue] = []
    n = len(entries)
    for i in range(n):
        for j in range(i + 1, n):
            a_id, a_path, a_methods = entries[i]
            b_id, b_path, b_methods = entries[j]
            if a_path != b_path:
                continue
            shared = a_methods & b_methods
            if not shared:
                continue
            issues.append(Issue(
                code="interface_duplicate",
                severity="error",
                message=(
                    f"接口重复：模块 {a_id} 与 {b_id} 均声明 {a_path} "
                    f"方法 {'/'.join(sorted(shared))}"
                ),
                module_id=a_id,
                detail={
                    "modules": [a_id, b_id],
                    "path": a_path,
                    "shared_methods": sorted(shared),
                    "a_methods": sorted(a_methods),
                    "b_methods": sorted(b_methods),
                },
            ))
    return issues


# 写操作动词（用于 method 语义兜底校验：path 命中这些词但 method 标 get → 疑似写操作误标读）
WRITE_METHOD_VERBS = ("create", "reply", "write", "delete", "remove", "add", "set", "update")

# 同类 method 语义错误 ≥ 该阈值 → 从 warning 升级为 error（反复犯 = 系统性错误，不是偶发）
METHOD_MISMATCH_ERROR_THRESHOLD = 2


def find_method_semantic_mismatch(modules: Sequence[Dict[str, Any]]) -> List[Issue]:
    """method 语义兜底校验：path 含写操作动词但 method 标了 GET → warning。

    这是对 planner 提示词「写操作禁标 get」的机器兜底（启发式，可能误报，故 warning 不阻断）。
    写操作误标 get 会导致下游按「读取」实现（mock 返回而非真写），契约直接不能对接。
    """
    issues: List[Issue] = []
    for m in modules:
        mid = m.get("id")
        if not isinstance(mid, str):
            continue
        for iface in m.get("interfaces") or []:
            if not isinstance(iface, dict):
                continue
            path = iface.get("path")
            if not isinstance(path, str) or not path.strip():
                continue
            methods = normalize_method(iface.get("method"))
            if "GET" not in methods:
                continue
            path_lower = path.lower()
            hits = [v for v in WRITE_METHOD_VERBS if v in path_lower]
            if not hits:
                continue
            issues.append(Issue(
                code="method_semantic_mismatch",
                severity="warning",
                module_id=mid,
                message=(
                    f"接口 {path} 疑似写操作（命中动词 {'/'.join(hits)}）但 method 标了 GET；"
                    f"写操作应用 post/push，避免下游误按读取实现"
                ),
                detail={"path": path, "method": sorted(methods), "hit_verbs": hits},
            ))
    # 反复犯同类错误（≥ 阈值）→ 升级为 error：说明不是偶发，是 planner 系统性错误，需阻断
    if len(issues) >= METHOD_MISMATCH_ERROR_THRESHOLD:
        issues = [dataclasses.replace(it, severity="error") for it in issues]
    return issues
