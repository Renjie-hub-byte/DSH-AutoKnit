"""fwapi.dsh.task —— 只读任务数据源。

提供两组互不干扰的数据源，均严格对齐 contract.yaml 的 data_shape 并确定性空降级：

A. 旧 mock 兼容数据源（/api/tasks*，runs.json）：
   - list_tasks / get_task_detail 读文档化约定文件 总日志/runs.json（见 README），
     目录缺失/文件缺失或损坏 → list 返回 []，detail 返回 None；字段缺失按默认补齐。
   - 仅作向后兼容保留，不动既有行为。

B. 真实快照数据源（/api/runs、/api/runs/{id}/tree，总日志/快照.json）：
   - list_runs 从真实快照聚合 run 列表（含 needs_human 模块清单），目录缺失确定性 []；
   - get_run_tree 返回执行树（modules / dependencies / per_module 全字段 / needs_human），
     split 子树经 per_module 的 parent_module / child_modules / split_depth 任意深度表达；
     run_id 未命中或目录无效确定性返回 None。
   - 单字段缺失用契约默认值补齐，绝不抛异常；未知标 unknown，禁止编造。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from fwapi import registry as registry_source

# 任务状态共享枚举（须与 contract.shared_enums.task_stage 对齐）。
STAGES: tuple = (
    "executor",
    "auditor",
    "switch",
    "needs_human",
    "planning",
    "unknown",
)

# stage → 人类可读阶段标签（stage_label 缺省来源）。
STAGE_LABELS: Dict[str, str] = {
    "executor": "执行中",
    "auditor": "审核中",
    "switch": "切换",
    "needs_human": "需人工",
    "planning": "规划中",
    "unknown": "未知",
}

# 任务目录下 run 状态存储的相对路径（文档化约定）。
RUNS_FILE = os.path.join("总日志", "runs.json")

# 真实快照文件相对路径（文档化约定，AutoKnit 运行期写入）。
SNAPSHOT_FILE = os.path.join("总日志", "快照.json")

# 事件流源文件相对路径（文档化约定，AutoKnit 运行期追加写入）。
DISPATCH_FILE = os.path.join("总日志", "dispatch.jsonl")

# dsh.task.timeline 契约声明的事件枚举（对齐 contract.yaml；timeline 只输出这些事件，
# 未枚举事件/无合法 seq 的事件确定性过滤，不编造、不落到 unknown）。
TIMELINE_EVENTS: tuple = (
    "run.start",
    "run.resume",
    "module.dispatch",
    "executor.round.start",
    "executor.round.done",
    "auditor.round.start",
    "auditor.round",
    "module.needs_human",
    "module.human_rerun",
    "module.split",
    "module.aggregated",
    "module.final_block",
    "module.done",
    "integration.check",
)

# dsh.task.tree 的 per_module 契约声明字段；缺失时用默认值补齐（extendable 保留全字段）。
PER_MODULE_FIELDS: tuple = (
    "executor_round",
    "auditor_round",
    "executor_id",
    "last_verdict",
    "reason",
    "split_depth",
    "parent_module",
    "child_modules",
    "tokens_used",
    "started_at",
    "ended_at",
)


def _is_valid_task_dir(task_dir: str) -> bool:
    """task_dir 有效当且仅当是非空字符串且指向一个真实存在的目录。"""
    return bool(task_dir) and os.path.isdir(task_dir)


def _read_runs(task_dir: str) -> List[Dict[str, Any]]:
    """读取 run 状态存储；目录缺失/文件缺失或损坏时确定性返回 []。"""
    if not _is_valid_task_dir(task_dir):
        return []
    path = os.path.join(task_dir, RUNS_FILE)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        return []
    runs = payload.get("runs", []) if isinstance(payload, dict) else payload
    return runs if isinstance(runs, list) else []


def _coerce_stage(raw: Any) -> str:
    """归一化 stage 到契约枚举；未知值确定性落到 'unknown'。"""
    return raw if raw in STAGES else "unknown"


def _stage_label(stage: str, raw_label: Any) -> str:
    """stage_label：优先用源数据显式值，否则用枚举映射缺省。"""
    if isinstance(raw_label, str) and raw_label:
        return raw_label
    return STAGE_LABELS.get(stage, stage)


def _coerce_int(raw: Any, default: int = 0) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _coerce_bool(raw: Any, default: bool = False) -> bool:
    if isinstance(raw, bool):
        return raw
    if raw in (0, 1):
        return bool(raw)
    return default


def _normalize_consumption(raw: Any) -> Dict[str, Any]:
    """归一化 consumption 字段，缺失项用 0 兜底，保持契约字段稳定。"""
    base: Dict[str, Any] = {
        "token_input": 0,
        "token_output": 0,
        "cache_hit": "no",
        "duration_sec": 0,
    }
    if isinstance(raw, dict):
        base["token_input"] = _coerce_int(raw.get("token_input"), 0)
        base["token_output"] = _coerce_int(raw.get("token_output"), 0)
        cache = raw.get("cache_hit", "no")
        base["cache_hit"] = cache if isinstance(cache, str) and cache else "no"
        base["duration_sec"] = _coerce_int(raw.get("duration_sec"), 0)
    return base


def _normalize_list_item(run: Any) -> Optional[Dict[str, Any]]:
    """把单个 run 归一化为 fwapi.tasks.list 的 item；缺 run_id 则丢弃。"""
    if not isinstance(run, dict):
        return None
    run_id = run.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        return None
    stage = _coerce_stage(run.get("stage"))
    return {
        "run_id": run_id,
        "stage": stage,
        "stage_label": _stage_label(stage, run.get("stage_label")),
        "module_states": run.get("module_states")
        if isinstance(run.get("module_states"), dict)
        else {},
        "urgency": _coerce_int(run.get("urgency"), 0),
        "needs_human": _coerce_bool(run.get("needs_human")),
        "consumption": _normalize_consumption(run.get("consumption")),
    }


def _normalize_detail(run: Dict[str, Any]) -> Dict[str, Any]:
    """把单个 run 归一化为 fwapi.tasks.detail 的字段集合。"""
    stage = _coerce_stage(run.get("stage"))
    return {
        "run_id": run.get("run_id"),
        "stage": stage,
        "stage_label": _stage_label(stage, run.get("stage_label")),
        "task_name": run.get("task_name")
        if isinstance(run.get("task_name"), str)
        else "",
        "module_states": run.get("module_states")
        if isinstance(run.get("module_states"), dict)
        else {},
        "needs_human": _coerce_bool(run.get("needs_human")),
    }


def list_tasks(task_dir: str) -> List[Dict[str, Any]]:
    """fwapi.tasks.list 数据源：按紧急度（urgency 降序）返回任务列表。

    目录缺失/无有效 run 时确定性空降级为 []。
    排序规则：urgency 大者优先；同紧急度按 run_id 升序保证确定性。
    """
    items: List[Dict[str, Any]] = []
    for run in _read_runs(task_dir):
        item = _normalize_list_item(run)
        if item is not None:
            items.append(item)
    items.sort(key=lambda it: (-it["urgency"], it["run_id"]))
    return items


def get_task_detail(task_dir: str, run_id: str) -> Optional[Dict[str, Any]]:
    """fwapi.tasks.detail 数据源：返回单个 run 详情；未命中/目录无效返回 None。"""
    if not run_id:
        return None
    for run in _read_runs(task_dir):
        if run.get("run_id") == run_id and isinstance(run, dict):
            return _normalize_detail(run)
    return None


# ============================================================ 真实快照数据源 ====


def _coerce_str(raw: Any, default: str = "") -> str:
    """把值归一化为字符串；非字符串确定性回落 default。"""
    return raw if isinstance(raw, str) else default


def _coerce_str_list(raw: Any) -> List[str]:
    """把值归一化为去重的非空字符串列表（模块 id 集合）。

    兼容两种形态：纯字符串列表（如 ["m03"]）或对象列表（含 module/id 键）。
    非列表输入确定性回落 []。
    """
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for it in raw:
        val: Any = None
        if isinstance(it, str):
            val = it
        elif isinstance(it, dict):
            val = it.get("module") or it.get("module_id") or it.get("id")
        if isinstance(val, str) and val and val not in out:
            out.append(val)
    return out


def _read_snapshot(task_dir: str) -> Optional[Dict[str, Any]]:
    """读取 总日志/快照.json；目录缺失/文件缺失或损坏确定性返回 None。"""
    if not _is_valid_task_dir(task_dir):
        return None
    path = os.path.join(task_dir, SNAPSHOT_FILE)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _derive_stage(status: str, needs_human_modules: List[str]) -> str:
    """由快照派生 stage（契约声明 uncertain，故不做硬枚举）。

    确定性规则：有需人工模块 → 'needs_human'；否则透传 status 字符串；
    status 缺失/非字符串 → 'unknown'（未知标 unknown，禁止编造）。
    """
    if needs_human_modules:
        return "needs_human"
    return status if isinstance(status, str) and status else "unknown"


def _normalize_run(snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """把单个快照归一化为 dsh.task.runs 的 item；缺 run_id 则丢弃。"""
    run_id = snapshot.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        return None
    needs_human_modules = _coerce_str_list(snapshot.get("needs_human"))
    status = _coerce_str(snapshot.get("status"), "unknown")
    return {
        "run_id": run_id,
        "task": _coerce_str(snapshot.get("task")),
        "status": status,
        "stage": _derive_stage(status, needs_human_modules),
        "cause": _coerce_str(snapshot.get("cause")),
        "updated_at": _coerce_str(snapshot.get("updated_at")),
        "needs_human_modules": needs_human_modules,
    }


def _normalize_per_module(raw: Any) -> Optional[Dict[str, Any]]:
    """把单个 per_module 记录归一化为 dsh.task.tree 的字段集。

    契约声明字段缺失时用默认补齐；同时保留源记录全部字段（extendable）。
    """
    if not isinstance(raw, dict):
        return None
    out: Dict[str, Any] = dict(raw)  # 保留全字段，为扩展留口
    out["executor_round"] = _coerce_int(raw.get("executor_round"))
    out["auditor_round"] = _coerce_int(raw.get("auditor_round"))
    out["executor_id"] = _coerce_str(raw.get("executor_id"))
    out["last_verdict"] = _coerce_str(raw.get("last_verdict"))
    out["reason"] = _coerce_str(raw.get("reason"))
    out["split_depth"] = _coerce_int(raw.get("split_depth"))
    out["parent_module"] = _coerce_str(raw.get("parent_module"))
    out["child_modules"] = _coerce_str_list(raw.get("child_modules"))
    out["tokens_used"] = _coerce_int(raw.get("tokens_used"))
    out["started_at"] = _coerce_str(raw.get("started_at"))
    out["ended_at"] = _coerce_str(raw.get("ended_at"))
    return out


def _snapshot_modules(snapshot: Dict[str, Any]) -> List[str]:
    """提取模块 id 列表：modules 为 dict（module→status）或 list 均兼容，保持源顺序。"""
    modules_raw = snapshot.get("modules")
    if isinstance(modules_raw, dict):
        return [m for m in modules_raw if isinstance(m, str)]
    if isinstance(modules_raw, list):
        return _coerce_str_list(modules_raw)
    return []


def _list_runs_single(task_dir: str) -> List[Dict[str, Any]]:
    """单 task_dir 快照回落：从真实快照聚合 run 列表（现有单快照行为）。

    目录缺失/快照缺失或损坏 → 确定性空降级 []。快照含有效 run_id 时返回单元素列表。
    """
    snapshot = _read_snapshot(task_dir)
    if snapshot is None:
        return []
    item = _normalize_run(snapshot)
    return [item] if item is not None else []


# run 状态排序优先级：active 优先（契约 runs.list 排序），其次 complete/archived/unknown。
_STATUS_PRIORITY: Dict[str, int] = {
    "active": 0,
    "complete": 1,
    "archived": 2,
    "unknown": 3,
}


def _registry_item(
    record: Dict[str, Any], snapshot: Optional[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """由注册表 record + 对应 task_dir 快照聚合出 dsh.runs.list 的 item。

    record（注册表为准）提供 run_id/task_dir/task/status/started_at/updated_at；
    snapshot 提供 needs_human_modules/task/cause 详情；snapshot 缺失时详情字段用默认补齐。
    缺 run_id 确定性返回 None（调用方据此丢弃）。
    """
    run_id = record.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        return None
    needs_human = _coerce_str_list(snapshot.get("needs_human")) if snapshot else []
    status = _coerce_str(record.get("status"), "unknown")
    return {
        "run_id": run_id,
        "task": _coerce_str(record.get("task"))
        or (_coerce_str(snapshot.get("task")) if snapshot else ""),
        "task_dir": _coerce_str(record.get("task_dir")),
        "status": status,
        "stage": _derive_stage(status, needs_human),
        "updated_at": _coerce_str(record.get("updated_at"))
        or (_coerce_str(snapshot.get("updated_at")) if snapshot else ""),
        "started_at": _coerce_str(record.get("started_at")),
        "needs_human_modules": needs_human,
    }


def _registry_detail(
    record: Dict[str, Any], snapshot: Optional[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """由注册表 record + 快照聚合出 dsh.runs.detail 的 item（在 list item 基础上补 cause）。"""
    item = _registry_item(record, snapshot)
    if item is None:
        return None
    item["cause"] = _coerce_str(snapshot.get("cause")) if snapshot else ""
    return item


def _aggregate_runs(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """聚合注册表多 run：每 run 从各自 task_dir 快照取详情。

    排序：active 优先、updated_at 降序（同优先级/同时刻按 run_id 升序保证确定性）。
    """
    items: List[Dict[str, Any]] = []
    for rec in records:
        # 归档 run 不再展示（dsh.runs.archive：标记 archived 后列表不显示）。
        if rec.get("status") == "archived":
            continue
        snapshot = (
            _read_snapshot(rec.get("task_dir", "")) if rec.get("task_dir") else None
        )
        item = _registry_item(rec, snapshot)
        if item is not None:
            items.append(item)
    items.sort(
        key=lambda it: (
            _STATUS_PRIORITY.get(it["status"], 3),
            -registry_source.parse_iso(it["updated_at"]),
            it["run_id"],
        )
    )
    return items


def _fallback_record(run_id: str, task_dir: str, snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """构造注册表缺失回落时的 record（以快照为准，started_at 未知标空串）。"""
    return {
        "run_id": run_id,
        "task_dir": task_dir,
        "task": _coerce_str(snapshot.get("task")),
        "status": _coerce_str(snapshot.get("status"), "unknown"),
        "started_at": "",
        "updated_at": _coerce_str(snapshot.get("updated_at")),
    }


def list_runs(task_dir: str = "") -> List[Dict[str, Any]]:
    """dsh.task.runs 数据源：注册表聚合多 run；注册表缺失/为空确定性回落单 task_dir。

    - 注册表存在有效记录 → 聚合注册表所有 run（active 优先、updated_at 降序），
      每 run 从各自 task_dir 快照取详情；
    - 注册表缺失/为空 → 回落单 task_dir 快照（现有单快照行为，不破坏）。
    """
    records = registry_source.read_records()
    if records:
        return _aggregate_runs(records)
    return _list_runs_single(task_dir)


def _resolve_run_task_dir(task_dir: str, run_id: str) -> Optional[str]:
    """按注册表解析 run_id 对应的 task_dir（供 tree/timeline/usage/detail/reply 复用）。

    返回值语义（确定性）：
    - 注册表存在记录但不含该 run_id → None（未命中，调用方据此空降级）；
    - 注册表含该 run_id → 其登记 task_dir（可能为空串/无效，调用方自行判空降级）；
    - 注册表缺失/为空 → task_dir（原样回落，现有单 task_dir 行为不破坏）。
    """
    if not run_id:
        return None
    records = registry_source.read_records()
    rec = next((r for r in records if r["run_id"] == run_id), None)
    if records and rec is None:
        return None
    if rec is not None:
        return rec.get("task_dir", "") or None
    return task_dir


def get_run_detail(task_dir: str, run_id: str) -> Optional[Dict[str, Any]]:
    """dsh.runs.detail 数据源：按注册表定位 run_id 的 task_dir → 从该 task_dir 快照取详情。

    确定性规则：
    - 注册表持有 run_id 且登记 task_dir 有效且快照 run_id 匹配 → 合并注册表 + 快照详情；
    - 注册表持有 run_id 但目录无效 / 快照 run_id 不匹配 → 确定性 None（契约：未命中/目录无效）；
    - 注册表存在但不含 run_id → 确定性 None（契约：注册表未命中）；
    - 注册表缺失/为空 → 确定性回落单 task_dir 快照（现网单 run 世界）。
    """
    if not run_id:
        return None
    tdir = _resolve_run_task_dir(task_dir, run_id)
    if not _is_valid_task_dir(tdir or ""):
        return None
    snapshot = _read_snapshot(tdir)
    if snapshot is None or snapshot.get("run_id") != run_id:
        return None
    rec = registry_source.get_record(run_id)
    if rec is None:
        # 注册表缺失/为空：回落单 task_dir 快照（以快照为准构造 record）。
        rec = _fallback_record(run_id, tdir, snapshot)
    return _registry_detail(rec, snapshot)


def _build_module_node(mid, modules_status, dependencies, per_module, seen=None):
    """把模块 id 拼成 dsh.task.tree 的对象节点（面板 buildModuleView 期望的形状）。

    修复 2026-08-29（跨模块契约不一致）：旧返回 id 字符串数组，面板 topoLayer /
    buildModuleView 按对象处理（id/status/dependencies/split/token_used…）→ 渲染空白。
    现在每个节点 = {id, name, status, dependencies, split(子树递归), + per_module 字段}。
    """
    seen = seen if seen is not None else set()
    key = str(mid)
    if key in seen:
        return None
    seen.add(key)
    pm = per_module.get(key) or {}
    deps = dependencies.get(key)
    deps = [str(d) for d in deps] if isinstance(deps, list) else []
    children = []
    for cid in (pm.get("child_modules") or []):
        child = _build_module_node(str(cid), modules_status, dependencies, per_module, seen)
        if child is not None:
            children.append(child)
    return {
        "id": key,
        "name": key,
        "status": modules_status.get(key, "") if isinstance(modules_status, dict) else "",
        "dependencies": deps,
        "split": children,
        "last_verdict": pm.get("last_verdict", ""),
        "reason": pm.get("reason", ""),
        "token_used": pm.get("tokens_used", 0),
        "started_at": pm.get("started_at", None),
        "ended_at": pm.get("ended_at", None),
        "executor_round": pm.get("executor_round", 0),
        "auditor_round": pm.get("auditor_round", 0),
        "executor_id": pm.get("executor_id", ""),
        "split_depth": pm.get("split_depth", 0),
        "parent_module": pm.get("parent_module", ""),
        "child_modules": [str(c) for c in (pm.get("child_modules") or [])],
    }


def get_run_tree(task_dir: str, run_id: str) -> Optional[Dict[str, Any]]:
    """dsh.task.tree 数据源：返回执行树；未命中/目录无效确定性返回 None。

    返回 {run_id, modules, dependencies, per_module, needs_human}。
    modules 为**对象数组**（id/status/dependencies/split 子树/per_module 字段），
    顶层只放 root 模块（无 parent_module），split 子模块递归挂在父节点 split 下，
    与面板 buildRouteMap / buildModuleView 契约一致（修复 2026-08-29）。
    """
    if not run_id:
        return None
    # 按注册表解析 task_dir（注册表未命中确定性 null；缺失回落请求级 task_dir）。
    tdir = _resolve_run_task_dir(task_dir, run_id)
    if not _is_valid_task_dir(tdir or ""):
        return None
    snapshot = _read_snapshot(tdir)
    if snapshot is None or snapshot.get("run_id") != run_id:
        return None

    dependencies = snapshot.get("dependencies")
    if not isinstance(dependencies, dict):
        dependencies = {}

    per_module_raw = snapshot.get("per_module")
    per_module: Dict[str, Any] = {}
    if isinstance(per_module_raw, dict):
        for mid, rec in per_module_raw.items():
            norm = _normalize_per_module(rec)
            if norm is not None:
                per_module[mid] = norm

    modules_raw = snapshot.get("modules")
    modules_status = modules_raw if isinstance(modules_raw, dict) else {}
    all_ids = _snapshot_modules(snapshot)
    # 顶层 = 无 parent_module 的 root 模块（split 子模块挂在父节点 split 下）
    roots = [m for m in all_ids if not (per_module.get(m) or {}).get("parent_module")]
    if not roots:
        roots = all_ids
    nodes: List[Dict[str, Any]] = []
    for mid in roots:
        node = _build_module_node(mid, modules_status, dependencies, per_module, set())
        if node is not None:
            nodes.append(node)

    return {
        "run_id": snapshot.get("run_id"),
        "modules": nodes,
        "dependencies": dependencies,
        "per_module": per_module,
        "needs_human": _coerce_str_list(snapshot.get("needs_human")),
        # human_answer.json 与快照同源（tdir=注册表定位的任务目录），不能用
        # 请求级 task_dir（前端不带 → 空串读错目录）。
        "human_answers": read_human_answers(tdir),
    }


def read_human_answers(task_dir: str) -> Dict[str, Dict[str, str]]:
    """读 总日志/human_answer.json → {mid: {code,text,answered_at,reason}}（只增字段）。

    决策卡片三态生命周期用：code 存在且非 '?' = 人已回复（resolved-by-human）；
    草稿/代填文本也在此透出（避免重复决策）。文件缺失/损坏/形态不符 → 确定性
    {}，绝不抛异常；未知字段丢弃（只透出契约四项）。
    """
    path = os.path.join(task_dir, "总日志", "human_answer.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        return {}
    answers = payload.get("answers") if isinstance(payload, dict) else None
    if not isinstance(answers, dict):
        return {}
    out: Dict[str, Dict[str, str]] = {}
    for mid, rec in answers.items():
        if not isinstance(rec, dict) or not isinstance(mid, str) or not mid:
            continue
        out[mid] = {
            "code": _coerce_str(rec.get("code")),
            "text": _coerce_str(rec.get("text")),
            "answered_at": _coerce_str(rec.get("answered_at")),
            "reason": _coerce_str(rec.get("reason")),
        }
    return out


# ============================================================ 事件流数据源 ====


def _coerce_seq(raw: Any) -> Optional[int]:
    """归一化 seq；非整数确定性回落 None（调用方据此跳过该事件，不落到 0 伪值）。"""
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _read_dispatch(task_dir: str) -> List[Dict[str, Any]]:
    """逐行读取 dispatch.jsonl；目录缺失/文件缺失或损坏确定性返回 []。"""
    if not _is_valid_task_dir(task_dir):
        return []
    path = os.path.join(task_dir, DISPATCH_FILE)
    if not os.path.isfile(path):
        return []
    out: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except ValueError:
                    continue
                if isinstance(raw, dict):
                    out.append(raw)
    except OSError:
        return []
    return out


def get_run_timeline(task_dir: str, run_id: str) -> List[Dict[str, Any]]:
    """dsh.task.timeline 数据源：返回 dispatch.jsonl 事件流按 seq 升序。

    仅输出「契约枚举内 + 带合法 seq + run_id 匹配」的事件，逐字段归一化
    （seq/ts/event/module/detail），缺失项用确定性默认补齐；
    目录/文件缺失、run_id 为空或未命中 → 确定性 []，绝不抛异常。
    """
    if not run_id:
        return []
    # 按注册表解析 task_dir（注册表未命中确定性 []; 缺失回落请求级 task_dir）。
    tdir = _resolve_run_task_dir(task_dir, run_id)
    if not _is_valid_task_dir(tdir or ""):
        return []
    events: List[Dict[str, Any]] = []
    for raw in _read_dispatch(tdir):
        if raw.get("run_id") != run_id:
            continue
        event = raw.get("event")
        if event not in TIMELINE_EVENTS:
            continue
        seq = _coerce_seq(raw.get("seq"))
        if seq is None:
            continue
        detail = raw.get("detail")
        events.append(
            {
                "seq": seq,
                "ts": _coerce_str(raw.get("ts")),
                "event": event,
                "module": _coerce_str(raw.get("module")),
                "detail": detail if isinstance(detail, dict) else {},
            }
        )
    events.sort(key=lambda e: e["seq"])
    return events
