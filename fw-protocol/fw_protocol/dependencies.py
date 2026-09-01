"""依赖校验：模块 id 唯一性 / 依赖引用未知模块 / 依赖重复 / 依赖环检测。

依赖环检测用三色 DFS（白/灰/黑）找回溯边，还原环路径（如 ["m01","m02","m03","m01"]），
并做规范化去重（环旋转到最小节点开头、同集去重）。任务书模块数很小（≤ 数十），
DFS 枚举简单环成本可忽略；仍设 limit=50 防病态输入爆量。
"""
from __future__ import annotations

from typing import Any, Dict, List, Sequence, Set, Tuple

from .model import Issue


def build_edges(modules: Sequence[Dict[str, Any]]) -> Dict[str, Set[str]]:
    """模块列表 → 邻接表 {module_id: set(dep_ids)}。跳过缺失 id 的模块（调用方先保证结构合法）。"""
    edges: Dict[str, Set[str]] = {}
    for m in modules:
        mid = m.get("id")
        if not isinstance(mid, str):
            continue
        deps = m.get("dependencies") or []
        edges.setdefault(mid, set())
        if isinstance(deps, list):
            for d in deps:
                if isinstance(d, str):
                    edges[mid].add(d)
    return edges


def _canonical(cycle: Sequence[str]) -> Tuple[str, ...]:
    """环规范化：旋转到最小节点在首位。用于去重（同一环不同起点只报一次）。"""
    if not cycle:
        return ()
    idx = min(range(len(cycle)), key=lambda i: cycle[i])
    return tuple(cycle[idx:] + cycle[:idx])


def find_all_simple_cycles(edges: Dict[str, Set[str]], limit: int = 50) -> List[List[str]]:
    """返回所有简单环（有向），每环为起点=终点的节点列表（如 ['m01','m02','m03','m01']）。

    实现：回溯 DFS 枚举所有简单路径，遇路径内节点（回溯边）即闭合为环；规范化去重。
    seen_global 剪枝：已完全展开过的节点不再作为路径中间节点展开。
    """
    cycles: List[List[str]] = []
    seen: Set[Tuple[str, ...]] = set()
    seen_global: Set[str] = set()
    nodes = sorted(edges.keys())

    def dfs(node: str, path: List[str], on_path: Set[str]) -> None:
        if len(cycles) >= limit:
            return
        for nxt in sorted(edges.get(node, ())):
            if nxt in on_path:  # 回溯边 → 环
                idx = path.index(nxt)
                cycle = path[idx:] + [nxt]
                canon = _canonical(cycle)
                if canon not in seen:
                    seen.add(canon)
                    cycles.append(list(cycle))
                    if len(cycles) >= limit:
                        return
            elif nxt not in seen_global:
                path.append(nxt)
                on_path.add(nxt)
                dfs(nxt, path, on_path)
                path.pop()
                on_path.discard(nxt)
        if len(cycles) < limit:
            seen_global.add(node)

    for start in nodes:
        if start in seen_global:
            continue
        dfs(start, [start], {start})
        if len(cycles) >= limit:
            break
    return cycles


def validate_dependencies(modules: Sequence[Dict[str, Any]]) -> List[Issue]:
    """依赖校验四查：id 唯一 / 依赖引用未知模块 / 依赖条目重复 / 依赖环（DFS）。"""
    issues: List[Issue] = []

    ids: List[str] = [m["id"] for m in modules if isinstance(m.get("id"), str)]
    id_set = set(ids)

    # 1) 模块 id 唯一
    seen_ids: Set[str] = set()
    for m in modules:
        mid = m.get("id")
        if not isinstance(mid, str):
            continue
        if mid in seen_ids:
            issues.append(Issue(
                code="module_id_duplicate",
                severity="error",
                message=f"模块 id 重复：{mid}",
                module_id=mid,
                detail={"module_id": mid},
            ))
        seen_ids.add(mid)

    # 2) 依赖引用未知模块 + 3) 依赖条目重复
    for m in modules:
        mid = m.get("id")
        if not isinstance(mid, str):
            continue
        deps = m.get("dependencies") or []
        if not isinstance(deps, list):
            continue
        seen_dep: Set[str] = set()
        for d in deps:
            if not isinstance(d, str):
                continue
            if d != mid and d not in id_set:
                issues.append(Issue(
                    code="dep_unknown_module",
                    severity="error",
                    message=f"模块 {mid} 依赖了未定义的模块 {d}",
                    module_id=mid,
                    detail={"module": mid, "unknown_dependency": d},
                ))
            if d in seen_dep:
                issues.append(Issue(
                    code="dep_duplicate",
                    severity="error",
                    message=f"模块 {mid} 的依赖列表重复出现 {d}",
                    module_id=mid,
                    detail={"module": mid, "duplicate_dependency": d},
                ))
            seen_dep.add(d)
        # 自依赖：DFS 会检成单节点环，这里不用重复报
    # 4) 依赖环（DFS）
    edges = build_edges(modules)
    for cycle in find_all_simple_cycles(edges):
        issues.append(Issue(
            code="dep_cycle",
            severity="error",
            message="依赖环: " + " → ".join(cycle),
            detail={"cycle": cycle},
        ))

    return issues
