"""依赖图拓扑分层 + 并行批次规划（需求4 核心调度逻辑）。

- topological_layers : Kahn 算法分层（layer 0 = 无依赖根层；层 k = 依赖全部落在更早层的模块）
- plan_batches        : 就绪集贪心分批 —— 每批最多 max_parallel 个模块，
  且每批只含"依赖已全部完成"的模块（严格满足：下游等上游**完成**才启动）。
  批间串行、批内并行 —— 这就是"同层独立模块并行 ≤ max_parallel"的落地形态。

输入为 fw-protocol 校验通过的任务书（环已被协议层拒绝）；这里保留防御性环检测。
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Mapping, Sequence


class CycleError(Exception):
    """依赖图存在环（防御性；合法任务书不应出现）。含环路径供报错。"""


def build_edges(modules: Sequence[Mapping[str, object]]) -> Dict[str, List[str]]:
    """module 序列 → {id: [deps]}（只保留出现在模块集合内的依赖）。"""
    ids = {m["id"] for m in modules if m.get("id")}
    edges: Dict[str, List[str]] = {}
    for m in modules:
        mid = m.get("id")
        if not mid:
            continue
        deps = [d for d in (m.get("dependencies") or []) if isinstance(d, str) and d in ids]
        edges[str(mid)] = sorted(set(deps))
    return edges


def _find_cycle(edges: Mapping[str, Sequence[str]], order: Sequence[str]) -> List[str]:
    """回溯 DFS 找任意一个简单环（防御性；合法任务书不会走到这里）。"""
    visited: Dict[str, int] = {}   # 0=未访问 1=在栈 2=完成

    def dfs(node: str, stack: List[str]) -> List[str]:
        visited[node] = 1
        stack.append(node)
        for nxt in edges.get(node, []):
            if visited.get(nxt, 0) == 1:
                i = stack.index(nxt)
                return stack[i:] + [nxt]
            if visited.get(nxt, 0) == 0:
                r = dfs(nxt, stack)
                if r:
                    return r
        stack.pop()
        visited[node] = 2
        return []

    for mid in order:
        if visited.get(mid, 0) == 0:
            r = dfs(mid, [])
            if r:
                return r
    return []


def _reverse_edges(edges: Mapping[str, Sequence[str]]) -> Dict[str, List[str]]:
    """反转邻接表：dep -> [依赖它的模块]。"""
    rev: Dict[str, List[str]] = {}
    for mid, deps in edges.items():
        for d in deps:
            rev.setdefault(d, []).append(mid)
    return rev


def topological_layers(modules: Sequence[Mapping[str, object]]) -> List[List[str]]:
    """Kahn 分层：返回 [[ids...], ...]，layer 0 为无依赖根层。

    环 → 抛 CycleError（带环路径）。层内顺序按任务书声明序稳定，便于测试确定性。
    """
    edges = build_edges(modules)
    order = [str(m["id"]) for m in modules if m.get("id")]
    cyc = _find_cycle(edges, order)
    if cyc:
        raise CycleError("依赖图存在环（防御性检测）: " + " → ".join(cyc))

    indeg = {mid: len(edges.get(mid, [])) for mid in order}   # 前置依赖数
    dependents = _reverse_edges(edges)
    ready = [mid for mid in order if indeg[mid] == 0]
    layers: List[List[str]] = []
    while ready:
        layer = list(ready)
        layers.append(layer)
        next_ready: List[str] = []
        for mid in layer:
            for u in dependents.get(mid, []):
                indeg[u] -= 1
                if indeg[u] == 0:
                    next_ready.append(u)
        seen: set = set()
        ready = [u for u in next_ready if not (u in seen or seen.add(u))]
    if sum(len(l) for l in layers) != len(order):
        raise CycleError("依赖图不完整（存在环或孤立异常）")
    return layers


def plan_batches(modules: Sequence[Mapping[str, object]],
                 max_parallel: int,
                 completed: Iterable[str] = ()) -> List[List[str]]:
    """就绪集贪心分批：每批 ≤ max_parallel 且依赖全部完成。

    completed：已完成的模块 id（resume 场景跳过，其依赖视为已满足）。
    返回批次序列 [[ids...], ...]；批内并行、批间串行。
    """
    if max_parallel < 1:
        raise ValueError("max_parallel 必须 >= 1")
    edges = build_edges(modules)
    order = [str(m["id"]) for m in modules if m.get("id")]
    cyc = _find_cycle(edges, order)
    if cyc:
        raise CycleError("依赖图存在环（防御性检测）: " + " → ".join(cyc))

    done = set(completed)
    remaining = [mid for mid in order if mid not in done]
    need = {mid: set(edges.get(mid, [])) for mid in remaining}
    batches: List[List[str]] = []
    while remaining:
        ready = [mid for mid in remaining
                 if not (need[mid] - done) and mid not in done]
        if not ready:
            raise CycleError("依赖图无法推进（剩余模块依赖未满足）: 剩余=" + ",".join(remaining))
        batch = ready[:max_parallel]
        batches.append(batch)
        for mid in batch:
            done.add(mid)
            remaining.remove(mid)
    return batches
