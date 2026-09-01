"""并行执行器（ForkRunner）—— dsh sessions.fork 的本地等价物 / 对接点。

- dsh 部署：把 run_parallel 换成 sessions.fork(scope=run_id, module=id) 继承公共上下文
  （fork 省缓存、事件溯源、token 记账均为 dsh 免费能力，runner 不自己实现）。
- 本地形态：ThreadPoolExecutor 并发（子进程驱动的 IO/计算天然释放 GIL 影响，
  满足测试可复现的确定性调度）。
"""
from __future__ import annotations

import concurrent.futures
from typing import Callable, List, TypeVar

T = TypeVar("T")


def run_parallel(workers: List[Callable[[], T]], max_concurrency: int = 0) -> List[T]:
    """并发执行一批 worker，保持入参顺序返回结果。

    max_concurrency：硬并发上限（同时活跃 worker 数 = 同时活跃的 dsh session 数）。
    0 或缺省 = 不额外限制（取 len(workers)）。
    这是「并发硬限制」的机制保证：即使调用方传入的 workers 超过上限，也最多 max_concurrency 个并发，
    不依赖上层分批逻辑的正确性（防电脑被并发 session 压垮）。

    任一 worker 抛异常 → 立即取消并上抛（由外层转 agent_error / interrupted 处理）。
    """
    if not workers:
        return []
    cap = len(workers)
    if max_concurrency and max_concurrency > 0:
        cap = min(cap, max_concurrency)
    with concurrent.futures.ThreadPoolExecutor(max_workers=cap) as pool:
        futures = [pool.submit(w) for w in workers]
        results = [f.result() for f in futures]
    return results
