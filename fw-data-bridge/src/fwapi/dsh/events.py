"""fwapi.dsh.events —— dsh.task.update 事件桥接（进程内轮询式）。

浏览器通过 GET /api/events?task_dir=...&since=<seq> 轮询任务状态增量更新。
事件在探测时通过 diff 产生，缓冲在进程内环形存储（按桶）。重启即清空 ——
进程内桥，不做持久化（符合本模块只写归档文件、只读任务状态的边界）。

事件载荷（对齐契约 dsh.task.update）：
    {"type":"task.update","run_id":str,"stage":str,"seq":int,"at":float,
     "removed":bool(可选，run 消失时为 true)}
    {"type":"run.start","run_id":str,"status":str,"seq":int,"at":float}      # 注册表新 run 出现
    {"type":"run.archived","run_id":str,"status":"archived","seq":int,"at":float}  # 注册表归档

seq 为全进程共享的单调递增游标（跨桶唯一），保证 /api/events 合并多桶后仍可
按 seq 做增量拉取；重启清零。
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, List

from fwapi import registry as registry_source
from fwapi.dsh import task as task_source

MAX_EVENTS = 500  # 每桶环形缓冲上限

# 注册表事件专用桶键：与任意 task_dir 分桶隔离（注册表为全进程共享存储，非某 task_dir 专属）。
_REGISTRY_BUCKET = "@registry"

_lock = threading.Lock()
_buckets: Dict[str, Dict[str, Any]] = {}
_seq = 0  # 全进程共享 seq 游标（跨桶单调递增，供合并后增量拉取）


def _bucket(key: str) -> Dict[str, Any]:
    """取/建某键的事件桶（{events}）；注册表桶额外持有 runs_snapshot）。"""
    key = key or ""
    bucket = _buckets.get(key)
    if bucket is None:
        bucket = {"events": []}
        _buckets[key] = bucket
    return bucket


def _emit(
    bucket: Dict[str, Any],
    type_: str,
    run_id: str,
    stage: str,
    status: str = "",
    removed: bool = False,
) -> Dict[str, Any]:
    """追加一条事件到桶，分配全局递增 seq 并截断环形缓冲。"""
    global _seq
    _seq += 1
    event: Dict[str, Any] = {
        "type": type_,
        "run_id": run_id,
        "stage": stage,
        "seq": _seq,
        "at": time.time(),
    }
    if status:
        event["status"] = status
    if removed:
        event["removed"] = True
    bucket["events"].append(event)
    if len(bucket["events"]) > MAX_EVENTS:
        bucket["events"] = bucket["events"][-MAX_EVENTS:]
    return event


def check_task_updates(task_dir: str) -> List[Dict[str, Any]]:
    """diff 当前 run 快照与上次观测，产出 task.update 事件并更新快照。

    幂等：连续调用两次相同状态不产生任何事件。
    """
    items = task_source.list_tasks(task_dir)
    current = {it["run_id"]: it["stage"] for it in items}

    with _lock:
        bucket = _bucket(task_dir)
        snapshot = bucket.get("snapshot", {})
        emitted: List[Dict[str, Any]] = []

        # 新增或 stage 变化 → 更新事件
        for run_id, stage in current.items():
            if snapshot.get(run_id) != stage:
                emitted.append(_emit(bucket, "task.update", run_id, stage))

        # 消失的 run → removed 事件
        for run_id in list(snapshot):
            if run_id not in current:
                emitted.append(
                    _emit(bucket, "task.update", run_id, "unknown", removed=True)
                )

        bucket["snapshot"] = current
        return emitted


# dispatch.jsonl 契约枚举事件（对齐 README 事件流章节；逐行解析时过滤）。
_DISPATCH_EVENT_NAMES = frozenset({
    "run.start", "module.dispatch", "executor.round.start", "executor.round.end",
    "executor.round.retry", "auditor.round.start", "auditor.round.end",
    "auditor.round.retry", "module.split", "module.aggregated",
    "module.final_block", "module.done", "integration.check",
})


def check_dispatch_events(task_dir: str) -> List[Dict[str, Any]]:
    """dispatch.jsonl 增量桥：真事件源，替代纯快照 diff 的滞后探测。

    fw-executor/auditor 每轮次结束向 <task_dir>/总日志/dispatch.jsonl 追加一条
    带 seq 的事件；本桥按字节 offset 增量读新行（stat 级成本），把契约枚举内
    事件映射为 task.update（前端 reducer 只认它就会刷新对应 run 的 tree/usage）。

    - 每批内按 run_id 去抖（0.5s 探测间隔内同 run 多事件合并为一条）；
    - 首次见到某 task_dir 桶时 offset 直接置为当前文件尾（跳历史，不重放）；
    - 文件被截断/重建（offset > size）→ offset 归零重读；
    - 行解析失败/无 seq/契约外事件 → 跳过该行，绝不抛异常。
    """
    path = os.path.join(task_dir, "总日志", "dispatch.jsonl")
    with _lock:
        bucket = _bucket(task_dir)
        first_seen = "dispatch_offset" not in bucket
        offset = bucket.get("dispatch_offset", 0)
    try:
        size = os.path.getsize(path)
    except OSError:
        # 文件尚未创建：落桶 offset=0（之后首次追加按增量整读，不丢事件）。
        if first_seen:
            with _lock:
                bucket["dispatch_offset"] = 0
        return []
    if first_seen:
        with _lock:
            bucket["dispatch_offset"] = size
        return []
    if size <= offset:
        if size < offset:
            with _lock:
                bucket["dispatch_offset"] = 0
        return []

    try:
        with open(path, "rb") as fh:
            fh.seek(offset)
            chunk = fh.read(size - offset)
    except OSError:
        return []
    new_offset = offset + len(chunk)
    text = chunk.decode("utf-8", errors="replace")

    # 批内按 run_id 去抖：只保留每个 run 最后一条契约事件。
    last_by_run: Dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        event = row.get("event")
        run_id = row.get("run_id")
        if event not in _DISPATCH_EVENT_NAMES or not isinstance(run_id, str) or not run_id:
            continue
        last_by_run[run_id] = str(event)

    with _lock:
        bucket["dispatch_offset"] = new_offset
        emitted = [
            _emit(bucket, "task.update", run_id, stage)
            for run_id, stage in sorted(last_by_run.items())
        ]
    return emitted


def check_human_answer_updates(task_dir: str) -> List[Dict[str, Any]]:
    """diff human_answer.json（mtime）→ 有新回复产出 task.update 事件。

    动机（2026-09-02 杰哥实测）：runner 只在 --resume 启动时消费 human_answer.json，
    运行中/退出后快照 needs_human 不变 → 长轮询无事件 → 面板「已解决」态永远刷不出来
    （人回复了石沉大海）。回复写入本身就该是一个事件：回复 → 面板立即可见已解决。
    幂等：mtime 未变不产生事件；首次观测（基线建立）不产生事件。
    """
    path = os.path.join(task_dir, "总日志", "human_answer.json")
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return []
    with _lock:
        bucket = _bucket(task_dir)
        last = bucket.get("human_answer_mtime", 0.0)
        bucket["human_answer_mtime"] = mtime
        if mtime <= last or last == 0.0:
            return []
        try:
            data = json.load(open(path, encoding="utf-8"))
            answers = data.get("answers") or {}
        except Exception:
            answers = {}
        # 定位事件归属 run：优先 stage=needs_human 的活跃 run，否则最新 run
        try:
            items = task_source.list_tasks(task_dir)
        except Exception:
            items = []
        target = next((it for it in items if it.get("stage") == "needs_human"),
                      items[0] if items else None)
        if target is None:
            return []
        emitted: List[Dict[str, Any]] = []
        for mid, ans in answers.items():
            code = str((ans or {}).get("code") or "?")
            if code and code != "?":
                emitted.append(_emit(bucket, "task.update", target["run_id"], "needs_human"))
        return emitted


def check_runs_updates() -> List[Dict[str, Any]]:
    """diff 注册表 run 状态与上次观测，产出 runs 级事件并更新快照。

    - 新 run 出现（注册表新 record）→ run.start；
    - run 状态转为 archived → run.archived；
    幂等：连续两次观测相同状态不产生任何事件。注册表缺失/为空 → 无事件。
    """
    records = registry_source.read_records()
    current = {r["run_id"]: r["status"] for r in records}

    with _lock:
        bucket = _bucket(_REGISTRY_BUCKET)
        # 注册表文件路径变化（测试隔离 / 部署切换指向另一份注册表）时，丢弃旧桶，
        # 避免把上一份注册表的历史事件串到当前查询（进程内桥按单注册表语义）。
        reg_path = registry_source.resolve_registry_path()
        if bucket.get("registry_path") != reg_path:
            bucket["events"] = []
            bucket.pop("runs_snapshot", None)
            bucket["registry_path"] = reg_path
        snapshot = bucket.get("runs_snapshot", {})
        emitted: List[Dict[str, Any]] = []

        for run_id, status in current.items():
            if run_id not in snapshot:
                # 新 run 出现 → run.start（无论初始 status）。
                # 归档重放防护（2026-09-01）：serve 重启后事件游标清零，历史
                # archived run 会被当成"新出现"重发 run.start——面板把归档 run
                # 渲染回来（归档复活的另一条路径）。archived 是终态，不发 start。
                if status == "archived":
                    continue
                emitted.append(_emit(bucket, "run.start", run_id, "", status=status))
            elif snapshot[run_id] != status and status == "archived":
                # 状态转为 archived → run.archived。
                emitted.append(_emit(bucket, "run.archived", run_id, "", status=status))

        bucket["runs_snapshot"] = current
        return emitted


def events_since(task_dir: str, since: int = 0) -> List[Dict[str, Any]]:
    """返回 seq > since 的缓冲事件（合并 task_dir 桶 + 注册表桶，按 seq 升序）；无则空列表。"""
    with _lock:
        buckets = [_bucket(task_dir), _bucket(_REGISTRY_BUCKET)]
        merged: List[Dict[str, Any]] = []
        for bucket in buckets:
            merged.extend(e for e in bucket["events"] if e["seq"] > since)
        merged.sort(key=lambda e: e["seq"])
        return merged


def reset_buckets() -> None:
    """清空全部事件桶与游标（测试隔离用；生产 serve 进程不调用）。"""
    global _seq
    with _lock:
        _buckets.clear()
        _seq = 0
