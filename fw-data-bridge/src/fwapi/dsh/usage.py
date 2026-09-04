"""fwapi.dsh.usage —— token 消耗汇总数据源。

两个互不干扰的数据源：
A. summary —— GET /api/usage（对 runs.json 的 consumption 聚合，按阶段分桶），
   向后兼容保留，不动既有行为（一期已验收）。
B. run_usage —— GET /api/runs/{id}/usage：基于会话索引（session_index）的
   run 级 + planner 级 + per-module 消耗。查询是索引直查（毫秒级），不再每次
   请求全扫/起子进程（补丁债「刷新慢」根治）。

口径（Owner拍板 2026-09-01，与 pi-ai provider 语义对齐）：
- 会话文件 inputTokens = 非缓存输入（pi-ai 层 input = prompt_tokens − cacheRead
  − cacheWrite），cacheReadTokens 独立上报；
- billable（计费）= input + output（非缓存）；缓存读单独一列，不计入计费；
- 总输入 total_input = input + cache_read；缓存命中率 cache_rate =
  cache_read / total_input（前端展示用）。

run 归属（根治串扰/双计/漏计）：
- 会话按 cwd 目录名前缀 + 结尾边界精确归属 task_dir（不再任意位置子串匹配）；
- 时间窗 [run_started_at, 下一个同 task_dir run 的 started_at) 上下界齐全，
  同任务目录多 run 的会话按行级时间精确切分（修复旧 run 吞新 run 会话）；
- 模块归属沿用 BUG-007 段匹配语义（modules 段后一段 == 模块 id 或其前缀）；
- 根级会话按 [run_start, 首模块起点) 归 planner、[首模块起点, 窗口终点) 归
  other（integration/总检等），单列透明展示，不再静默丢弃（补丁债「部分
  数据没统计」根治）。

空降级：目录缺失 / run 未命中 / 无拆分数据 → 确定性结构全 0，绝不抛异常。
仅做只读聚合，绝不修改任务状态文件；不调 LLM。
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fwapi import registry as registry_source
from fwapi.dsh import task as task_source
from fwapi.dsh.session_index import (
    SessionIndex,
    encode_task_dir,
    get_index,
)

# 契约 dsh.task.usage 声明的字段（run 级与 per-module 一致，extendable 保留扩展）。
TOKEN_FIELDS: tuple = ("input", "output", "cache_read", "calls", "billable")

# 旧 run 无拆分数据的标记（对齐契约 no_split: str）。
NO_SPLIT = "无拆分数据"


def summary(task_dir: str) -> Dict[str, Any]:
    """dsh.usage.summary：聚合全 run 的 token / duration / cache 消耗。"""
    items = task_source.list_tasks(task_dir)

    total_input = 0
    total_output = 0
    total_duration = 0
    cache_hits = 0
    by_stage: Dict[str, Dict[str, Any]] = {}

    for it in items:
        stage = it["stage"]
        bucket = by_stage.setdefault(stage, {"runs": 0, "token_input": 0, "token_output": 0})
        bucket["runs"] += 1

        cons = it["consumption"]
        ti = task_source._coerce_int(cons.get("token_input"), 0)
        to = task_source._coerce_int(cons.get("token_output"), 0)
        ds = task_source._coerce_int(cons.get("duration_sec"), 0)
        bucket["token_input"] += ti
        bucket["token_output"] += to
        total_input += ti
        total_output += to
        total_duration += ds

        if cons.get("cache_hit") not in (None, "", "no"):
            cache_hits += 1

    by_stage = dict(sorted(by_stage.items()))

    return {
        "task_dir": task_dir or "",
        "total_runs": len(items),
        "total_token_input": total_input,
        "total_token_output": total_output,
        "total_duration_sec": total_duration,
        "cache_hit_runs": cache_hits,
        "by_stage": by_stage,
    }


# ====================================================== run 级 usage（/api/runs/{id}/usage） ====


def _empty_token() -> Dict[str, int]:
    """契约字段全 0 的确定性 token 桶。"""
    return {f: 0 for f in TOKEN_FIELDS}


def _to_ms(iso: Any) -> int:
    """ISO 时间串 → 毫秒时间戳；非字符串/解析失败确定性回落 0（不过滤）。

    兼容带时区偏移（如 +08:00）的 ISO8601；datetime.fromisoformat 在 py3.11+ 原生支持。
    """
    if not isinstance(iso, str) or not iso:
        return 0
    try:
        return int(datetime.fromisoformat(iso).timestamp() * 1000)
    except (ValueError, OverflowError, OSError):
        return 0


def _belongs_to_task(decoded_dirname: str, task_enc: str) -> bool:
    """会话 cwd 是否落在任务目录内：前缀 + 结尾边界判定。

    会话目录名 = "--" + cwd 编码路径 + "--"；task_enc = 任务目录按同规则编码
    （/ → -）。cwd 必须是任务目录本身或其子目录：
      decoded == "--" + task_enc            （cwd == 任务目录）
      decoded.startswith("--" + task_enc + "-")（cwd == 任务目录子目录）
    相比旧 --cwd 任意位置子串匹配，消除「任务路径片段出现在目录名中段」的误报。
    """
    if not task_enc:
        return False
    prefix = "--" + task_enc
    return decoded_dirname == prefix or decoded_dirname.startswith(prefix + "-")


def _root_belongs(decoded_dirname: str, task_dir: str) -> bool:
    """根级会话 cwd 是否属于本 run 的目录范围。

    真实布局（2026-09-01 实测 bench run）：planner 以 fw-run 的启动 cwd 为会话
    cwd = 任务目录的**父目录**；integration/总检可能落在任务目录本身。两者都是
    精确目录（目录名首尾 -- 包裹、无子路径），故用精确相等判定——绝不收其它
    目录（用户工作区/无关项目根）的根级会话。
    """
    if not task_dir:
        return False
    clean = task_dir.rstrip("/")
    self_enc = "--" + encode_task_dir(clean) + "--"
    parent_enc = "--" + encode_task_dir(os.path.dirname(clean)) + "--"
    return decoded_dirname == self_enc or decoded_dirname == parent_enc


def _run_windows(task_dir: str, run_id: str, snapshot: Dict[str, Any]) -> Tuple[int, int, int, int]:
    """run 时间窗四元组 (since_ms, until_ms, first_module_ms, terminal_ms)。

    - since：run 起点。注册表 started_at 优先（fw-run 启动即登记）；缺失回退
      per_module 最早模块起点；再缺失 → 0（模块聚合不过滤，兼容旧 run）。
    - until：模块/other 行的窗口上界 = min(下一个同 task_dir run 起点, run 终态
      时间)。同任务目录多 run 时把本 run 与后续 run 的会话精确切开（修复旧
      run 吞新 run 会话的双计）；run 终态时间 = registry updated_at（非 active，
      fw-runner 收官刷新）→ 防止 complete run 吸进收官后的无关会话。0 = 无上界
      （active run 进行中）。
    - first_module：per_module 最早模块起点；无 → 0（进行中，根级会话全归
      planner 窗口）。
    """
    per_module = snapshot.get("per_module")
    module_start = 0
    if isinstance(per_module, dict):
        starts = [_to_ms(rec.get("started_at")) for rec in per_module.values()
                  if isinstance(rec, dict)]
        starts = [s for s in starts if s > 0]
        if starts:
            module_start = min(starts)

    rec = registry_source.get_record(run_id)
    reg_start = _to_ms(rec.get("started_at")) if rec else 0
    since = reg_start or module_start
    if since == 0:
        return 0, 0, 0, 0

    # 上界候选：下一个同 task_dir run 起点 + 本 run 终态时间（非 active）。
    bounds: List[int] = []
    tdir_norm = os.path.normpath(task_dir) if task_dir else ""
    for r in registry_source.read_records():
        if os.path.normpath(r.get("task_dir", "")) != tdir_norm or r.get("run_id") == run_id:
            continue
        ms = _to_ms(r.get("started_at"))
        if ms > since:
            bounds.append(ms)
    if rec is not None and rec.get("status") != "active":
        terminal = _to_ms(rec.get("updated_at"))
        if terminal > since:
            bounds.append(terminal)
    until = min(bounds) if bounds else 0
    return since, until, module_start, until


def _add_rows(bucket: Dict[str, int], rows: List[Tuple[int, int, int, int]],
              since: int, until: int) -> Tuple[int, int]:
    """把窗口内的 usage 行累入桶；返回 (窗口内 tmin, tmax) 供 duration 计算。

    行级时间过滤：since ≤ t < until（until=0 表示无上界）。
    """
    tmin = 0
    tmax = 0
    for t, i, o, c in rows:
        if t < since:
            continue
        if until and t >= until:
            continue
        bucket["input"] += i
        bucket["output"] += o
        bucket["cache_read"] += c
        bucket["calls"] += 1
        if tmin == 0 or t < tmin:
            tmin = t
        if tmax == 0 or t > tmax:
            tmax = t
    return tmin, tmax


def _finish(bucket: Dict[str, int], tmin: int, tmax: int) -> Dict[str, Any]:
    """补齐口径字段：billable（input+output）、total_input、cache_rate、duration_ms。

    口径源头：pi-ai provider 层 input = prompt_tokens − cacheRead（非缓存输入），
    billable（Owner拍板 2026-09-01）= 非缓存输入 + 输出；缓存读单独一列不计费。
    """
    out = dict(bucket)
    out["billable"] = out["input"] + out["output"]
    total_input = out["input"] + out["cache_read"]
    out["total_input"] = total_input
    out["cache_rate"] = round(out["cache_read"] / total_input, 4) if total_input > 0 else 0.0
    out["duration_ms"] = (tmax - tmin) if (tmin and tmax and tmax > tmin) else 0
    return out


def _module_match(module_seg: str, module_ids: List[str]) -> Optional[str]:
    """会话模块段归属哪个契约模块 id：段相等或该段以 id + '-' 为前缀（BUG-007 语义）。

    严格段匹配不串扰：m03 不命中 m03a 目录；split 子模块 m01a-xxx 归 m01a。
    命中多个契约 id 不可能（id 列表本身互不为前缀冲突时）；按快照键序取首个。
    """
    if not module_seg:
        return None
    for mid in module_ids:
        if module_seg == mid or module_seg.startswith(mid + "-"):
            return mid
    return None


def _aggregate_run(index: SessionIndex, task_dir: str, module_ids: List[str],
                   since: int, until: int, first_module: int) -> Dict[str, Any]:
    """索引直查：一次遍历产出 run/planner/other/per_module 全部桶。

    会话归属判定：
    1. 模块会话：cwd 前缀 + 边界 → 属于本 run 的 task_dir；modules 段匹配契约
       模块 id；行级时间窗 [since, until)（until 含下一 run 起点 / run 终态）。
    2. 根级会话：cwd 精确 ∈ {任务目录, 父目录}（planner 真实布局，见
       _root_belongs）；first_module 已知时 planner = 「结束 ≤ first_module 的
       最近一个会话」（整会话全收——planner 结束晚于 registry 登记是常态，
       不能用 since 行级下界切）；其余根级行归 other，行级窗口
       [first_module, until)。first_module=0（进行中）→ 根级行全归 planner，
       行级窗口 [since, until)。
    """
    task_enc = encode_task_dir(task_dir)
    run_bucket = _empty_token()
    planner_bucket = _empty_token()
    other_bucket = _empty_token()
    per_module: Dict[str, Dict[str, int]] = {mid: _empty_token() for mid in module_ids}
    run_span: List[int] = [0, 0]     # [tmin, tmax]（run 全部会话）
    planner_span: List[int] = [0, 0]
    other_span: List[int] = [0, 0]
    module_span: Dict[str, List[int]] = {mid: [0, 0] for mid in module_ids}

    def _span(span: List[int], tmin: int, tmax: int) -> None:
        if tmin and (span[0] == 0 or tmin < span[0]):
            span[0] = tmin
        if tmax and (span[1] == 0 or tmax > span[1]):
            span[1] = tmax

    # planner 候选（first_module 已知时）：cwd 限定 + 结束 ≤ first_module 的
    # 会话中取结束最晚的一个（整会话全收）。
    planner_candidates: List[Tuple[int, List[Tuple[int, int, int, int]]]] = []

    for entry in index.iter_entries():
        if not entry.module_seg:
            if not _root_belongs(entry.decoded, task_dir):
                continue
            if not entry.rows:
                continue
            if first_module > 0:
                # 首模块起点已知 → planner = cwd 限定 + 结束 ≤ first_module 的
                # 最近一个会话（整会话全收）。不能用 since 行级下界切：planner
                # 会话结束早于 registry 登记时刻是常态，且真实数据里
                # first_module == since（登记与 dispatch 同一刻），行级窗口会把
                # planner 会话全部滤掉（planner 恒 0 的根因，2026-09-01 实测）。
                sess_tmax = max(t for t, _i, _o, _c in entry.rows)
                if sess_tmax <= first_module:
                    planner_candidates.append((sess_tmax, entry.rows))
                else:
                    t2 = _add_rows(other_bucket, entry.rows, first_module, until)
                    _span(other_span, *t2)
            else:
                if since == 0:
                    # run 起点未知（registry 无 started_at 的旧 run）：根级会话
                    # 无法按窗口归属，宁可不算也不错算（模块聚合不受影响）。
                    continue
                t1 = _add_rows(planner_bucket, entry.rows, since, until)
                _span(planner_span, *t1)
            continue
        if not _belongs_to_task(entry.decoded, task_enc):
            continue
        mid = _module_match(entry.module_seg, module_ids)
        if mid is None:
            continue
        t1 = _add_rows(per_module[mid], entry.rows, since, until)
        _span(module_span[mid], *t1)

    # planner 定稿：结束最晚的候选会话整会话全收（「最近一个」语义，同父目录
    # 相邻 run 的 planner 更靠近各自的首模块，不会互相吞）。
    if planner_candidates:
        best_rows = max(planner_candidates, key=lambda x: x[0])[1]
        tmin = min(t for t, _i, _o, _c in best_rows)
        tmax = max(t for t, _i, _o, _c in best_rows)
        for t, i, o, c in best_rows:
            planner_bucket["input"] += i
            planner_bucket["output"] += o
            planner_bucket["cache_read"] += c
            planner_bucket["calls"] += 1
        _span(planner_span, tmin, tmax)

    # run 级 = planner + 全部模块 + other（总消耗全包含；分项齐全可对账）。
    def _merge(dst: Dict[str, int], src: Dict[str, int]) -> None:
        for f in TOKEN_FIELDS:
            dst[f] += src[f]

    _merge(run_bucket, planner_bucket)
    for mid in module_ids:
        _merge(run_bucket, per_module[mid])
    _merge(run_bucket, other_bucket)
    _span(run_span, *planner_span)
    for mid in module_ids:
        _span(run_span, *module_span[mid])
    _span(run_span, *other_span)

    return {
        "run": _finish(run_bucket, run_span[0], run_span[1]),
        "planner": _finish(planner_bucket, planner_span[0], planner_span[1]),
        "other": _finish(other_bucket, other_span[0], other_span[1]),
        "per_module": {mid: _finish(per_module[mid], *module_span[mid]) for mid in module_ids},
    }


def run_usage(task_dir: str, run_id: str, index: Optional[SessionIndex] = None) -> Dict[str, Any]:
    """dsh.task.usage：run 级 + planner 级 + per-module 的 token 消耗（索引直查）。

    输出契约（extendable）：
        {run: {input,output,cache_read,calls,billable,total_input,cache_rate,duration_ms},
         planner: {<同上>},           # 规划阶段（根级会话，首模块起点之前）
         other:   {<同上>},           # 根级会话（首模块起点之后：integration/总检等）
         per_module: {<module_id>: <同上>},
         no_split: str}
    run 级 = planner + Σper_module + other（全包含，分项可对账）。
    空降级：目录缺失 / run 未命中 / 无拆分数据（快照无模块可拆）→ run/planner/other
    全 0、per_module 空、no_split=「无拆分数据」。绝不抛异常。
    """
    empty = {"run": _finish(_empty_token(), 0, 0),
             "planner": _finish(_empty_token(), 0, 0),
             "other": _finish(_empty_token(), 0, 0),
             "per_module": {}, "no_split": NO_SPLIT}

    if not run_id:
        return empty

    # 按注册表解析 task_dir（注册表未命中确定性空降级；缺失回落请求级 task_dir）。
    tdir = task_source._resolve_run_task_dir(task_dir, run_id)
    if not tdir:
        return empty

    snapshot = task_source._read_snapshot(tdir)
    if snapshot is None or snapshot.get("run_id") != run_id:
        return empty

    per_module = snapshot.get("per_module")
    if isinstance(per_module, dict) and per_module:
        modules = [mid for mid in per_module if isinstance(mid, str)]
    else:
        modules = task_source._snapshot_modules(snapshot)
    if not modules:
        return empty

    since, until, first_module, _terminal = _run_windows(tdir, run_id, snapshot)
    # since=0（registry 与快照均无 started_at 的旧 run）：模块聚合不过滤（与旧版
    # 行为一致，不丢数据），根级会话跳过（_aggregate_run 内判定）。

    idx = index if index is not None else get_index()
    result = _aggregate_run(idx, tdir, modules, since, until, first_module)
    result["no_split"] = ""
    return result
