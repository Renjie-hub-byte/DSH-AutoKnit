"""fwr_status.status —— DSH 阶段与模块状态计算（m02 拆分出的子模块 m02b 的 fwr.status.compute 实现）。

契约（contracts/api.yaml）：path=fwr.status.compute, method=send
  —— 由原始数据计算 run 阶段状态与模块状态，供查询服务/广播模块调用。

继承关系：本模块为父模块 m02 拆分出的子模块 m02b（换人中与 needs_human 状态识别线）。
父模块 m02 已交付 executor 执行中 / auditor 验收中 / 确定性降级（28 条 pytest 全过），
本文件为父模块 src/fwr_status/status.py 的完整继承 + 本轮扩展：
  - 新增「换人中」（switch）识别（任务书验收 4）
  - 新增「needs_human」（需人工介入）识别（任务书验收 5）
  - 既有 executor 执行中 / auditor 验收中 / 透传 / 降级行为原样保留（无回归）

输入：m01 `fwr.dir.read` 产出的结构化原始数据 dict（字段见 m01 contract.yaml）：
    {ok, task_dir, task, modules, runs:[{run_id, index, snapshot}], snapshot,
     dispatch_events, dispatch_count, missing, errors}

输出：确定性结构化 dict，字段固定（执行期涌现样例，见 contract.yaml 回填）：
    {ok, task_dir, runs: [RunStatus...], errors}
    RunStatus（本轮字段）：
      run_id             run 标识（透传 m01）
      index              原始快照序号（透传 m01）
      phase              原始阶段（snapshot.phase，如 executor/auditor/planning/switch/needs_human）
      status             原始状态（snapshot.status，如 running/reviewing/switching）
      module             当前模块（snapshot.module；缺省取该 run 最近一次派发事件的 module）
      updated_at         快照更新时间（透传，可缺省）
      stage              计算出的 DSH 阶段键：
                         needs_human | switch | executor | auditor | 其它阶段透传 | unknown
      stage_label        中文标签：'needs_human（需人工介入）' / '换人中' /
                         'executor 执行中' / 'auditor 验收中' / '待识别: <phase>' / '待识别'
      executor_running   是否为 executor 执行中（bool）
      auditor_reviewing  是否为 auditor 验收中（bool）
      switch_in_progress 是否为换人中（bool，本轮新增）
      needs_human        是否需要人工介入（bool，本轮新增）
      module_states      模块级状态表（snapshot.modules dict 透传；无则 None）

换人中（switch）识别规则（确定性、可测试，字段由执行期涌现后回填）：
  命中以下任一即判 stage="switch" / stage_label="换人中" / switch_in_progress=True：
    a) snapshot.phase（规范化后）∈ SWITCH_PHASE_TOKENS
       —— 覆盖 {"switch", "switching", "换人", "换人中", "handover", "reassign"} 等；
    b) snapshot.status（规范化后）∈ SWITCH_STATUS_TOKENS
       —— 覆盖 {"switching", "switch_executor", "换人中", "handover", "reassign"} 等；
    c) 快照无 phase/status 信号时，dispatch 事件兜底：该 run 存在事件名
       （event/type/kind，规范化后）含换人标记（switch/reassign/换人/handover）
       且事件名不含完成标记（done/complete/完成）的事件 → 换人中。
   优先级：needs_human > switch > executor/auditor（换人/需人工为更紧急状态，优先判定）。

needs_human 识别规则（确定性、可测试）：
  命中以下任一即判 stage="needs_human" / stage_label="needs_human（需人工介入）" / needs_human=True：
    a) snapshot.phase 或 snapshot.status（规范化后）∈ NEEDS_HUMAN_TOKENS
       —— 覆盖 {"needs_human", "needs-human", "waiting_human", "waiting-human",
                "blocked_human", "人工介入", "需要人工"} 等；
    b) snapshot.needs_human 字段为布尔 True（快照级显式标记）；
    c) snapshot.needs_human 为非空 list，且列表内某字符串等于该 run 的 run_id 或 module
       （快照级模块标记形态：needs_human: ["m03"] → 该 run 需要人工介入）。

降级规则（确定性，不抛异常，与父模块一致）：
  - 输入为 None / 非 dict / 缺 runs 键 / m01 ok=False → 确定性空结果（ok=False, runs=[]）
  - 有效输入但 runs 为空 → ok=True, runs=[]
  - 单条 run 快照异常（非 dict）→ 跳过该条并记 errors，不抛异常
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# 常量：阶段/状态词表（执行期涌现样例，测试与实现共用同一套）
# ---------------------------------------------------------------------------

# snapshot.phase 的 executor 阶段下，视为"执行中"的 status 取值（父模块已交付，原样保留）
EXECUTOR_RUNNING_STATUSES = {"running", "working", "in_progress", "executing", "started"}
# snapshot.phase 的 auditor 阶段下，视为"验收中"的 status 取值（父模块已交付，原样保留）
AUDITOR_REVIEWING_STATUSES = {"reviewing", "checking", "accepting", "running", "started"}

# 换人中：snapshot.phase 命中这些取值（规范化后精确匹配，大小写敏感）
SWITCH_PHASE_TOKENS = {
    "switch", "switching", "换人", "换人中", "handover", "reassign",
}
# 换人中：snapshot.status 命中这些取值（规范化后精确匹配，大小写敏感）
SWITCH_STATUS_TOKENS = {
    "switching", "switch_executor", "executor_switching",
    "换人", "换人中", "handover", "reassign",
}
# 换人中：dispatch 事件名（规范化后）含这些标记即视为换人信号（子串匹配，大小写敏感）
SWITCH_EVENT_MARKERS = ("switch", "reassign", "换人", "handover")
# 换人中：事件名含这些完成标记则不算"换人中"（switch.done 等终态事件）
SWITCH_DONE_MARKERS = ("done", "complete", "完成")
# 事件名可以出现在这些键上（任一）
EVENT_NAME_KEYS = ("event", "type", "kind")

# needs_human：snapshot.phase 或 snapshot.status 命中这些取值（规范化后精确匹配，大小写敏感）
NEEDS_HUMAN_TOKENS = {
    "needs_human", "needs-human", "waiting_human", "waiting-human",
    "blocked_human", "人工介入", "需要人工",
}

STAGE_EXECUTOR = "executor"
STAGE_AUDITOR = "auditor"
STAGE_SWITCH = "switch"
STAGE_NEEDS_HUMAN = "needs_human"
STAGE_UNKNOWN = "unknown"

LABEL_EXECUTOR_RUNNING = "executor 执行中"
LABEL_AUDITOR_REVIEWING = "auditor 验收中"
LABEL_SWITCH = "换人中"
LABEL_NEEDS_HUMAN = "needs_human（需人工介入）"
LABEL_UNKNOWN = "待识别"

# ---------------------------------------------------------------------------
# 确定性空结果
# ---------------------------------------------------------------------------

def empty_result(task_dir: Any = None) -> Dict[str, Any]:
    """确定性空结果：输入不可用（None/非 dict/ok=False）时返回，结构固定不抛异常。"""
    return {
        "ok": False,
        "task_dir": str(task_dir) if task_dir is not None else None,
        "runs": [],
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 单条 run 状态计算
# ---------------------------------------------------------------------------

def _norm(value: Any) -> Optional[str]:
    """把原始字段规范化为非空字符串；None/非字符串/空串 → None。

    与父模块 m02 语义一致：仅去首尾空白、**不做大小写转换**（updated_at 等
    透传字段必须原样保留；phase/status 词表匹配为大小写敏感精确匹配）。
    """
    if isinstance(value, str):
        return value.strip() or None
    return None


def _snapshot_dict(run: Dict[str, Any]) -> Dict[str, Any]:
    """取 run 的 snapshot 字典；非 dict → 空 dict（由调用方判定错误）。"""
    snap = run.get("snapshot")
    if isinstance(snap, dict):
        return snap
    return {}


def _latest_dispatch_module(run_id: str, dispatch_events: List[Any]) -> Optional[str]:
    """从 dispatch.jsonl 事件里取该 run 最近一次带 module 字段的派发事件的 module。

    仅作 snapshot.module 缺省时的兜底：逆序遍历，取第一个 dict 且 run_id 匹配
    且 module 为字符串的事件。
    """
    if not isinstance(dispatch_events, list):
        return None
    for event in reversed(dispatch_events):
        if not isinstance(event, dict):
            continue
        if event.get("run_id") != run_id:
            continue
        module = _norm(event.get("module"))
        if module is not None:
            return module
    return None


def _detect_needs_human(snap: Dict[str, Any], run_id: str, module: Optional[str]) -> bool:
    """判定该 run 是否 needs_human（需人工介入）。确定性：非预期形态一律 False。"""
    # a) phase/status 命中词表
    for key in ("phase", "status"):
        val = _norm(snap.get(key))
        if val is not None and val in NEEDS_HUMAN_TOKENS:
            return True
    # b) snapshot.needs_human 为布尔 True（快照级显式标记）
    if snap.get("needs_human") is True:
        return True
    # c) snapshot.needs_human 为非空 list 且包含该 run 的 run_id 或 module
    nh = snap.get("needs_human")
    if isinstance(nh, list) and nh:
        ids = {run_id, module} if module is not None else {run_id}
        for item in nh:
            if isinstance(item, str) and item.strip() in ids:
                return True
    return False


def _detect_switch(snap: Dict[str, Any], run_id: str, dispatch_events: Any) -> bool:
    """判定该 run 是否换人中（switch）。确定性：先快照词表，再 dispatch 事件兜底。"""
    # a) snapshot.phase 命中换人阶段词表
    phase = _norm(snap.get("phase"))
    if phase is not None and phase in SWITCH_PHASE_TOKENS:
        return True
    # b) snapshot.status 命中换人状态词表
    status = _norm(snap.get("status"))
    if status is not None and status in SWITCH_STATUS_TOKENS:
        return True
    # c) dispatch 事件兜底：该 run 存在事件名含换人标记且非完成标记的事件
    if not isinstance(dispatch_events, list):
        return False
    for event in dispatch_events:
        if not isinstance(event, dict):
            continue
        if event.get("run_id") != run_id:
            continue
        if "parse_error" in event:
            continue
        name: Optional[str] = None
        for key in EVENT_NAME_KEYS:
            name = _norm(event.get(key))
            if name is not None:
                break
        if name is None:
            continue
        if any(marker in name for marker in SWITCH_DONE_MARKERS):
            continue
        if any(marker in name for marker in SWITCH_EVENT_MARKERS):
            return True
    return False


def compute_run(
    run: Dict[str, Any],
    dispatch_events: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """计算单条 run 的阶段/模块状态（executor 执行中 / auditor 验收中 / 换人中 / needs_human）。"""
    run_id = _norm(run.get("run_id")) or "run-unknown"
    snap = _snapshot_dict(run)

    phase = _norm(snap.get("phase"))
    status = _norm(snap.get("status"))
    module = _norm(snap.get("module"))
    if module is None:
        module = _latest_dispatch_module(run_id, dispatch_events or [])
    updated_at = _norm(snap.get("updated_at"))
    module_states = snap.get("modules") if isinstance(snap.get("modules"), dict) else None

    # 阶段判定：优先级 needs_human > switch > executor/auditor > 其它透传 > unknown
    stage: str = STAGE_UNKNOWN
    stage_label: str = LABEL_UNKNOWN
    executor_running = False
    auditor_reviewing = False
    switch_in_progress = False
    needs_human = False

    if _detect_needs_human(snap, run_id, module):
        # needs_human：需人工介入（最紧急，优先判定；可能是换人过程升级或独立信号）
        stage, stage_label = STAGE_NEEDS_HUMAN, LABEL_NEEDS_HUMAN
        needs_human = True
    elif _detect_switch(snap, run_id, dispatch_events):
        # 换人中：正在切换 executor（快照词表或 dispatch 事件兜底）
        stage, stage_label = STAGE_SWITCH, LABEL_SWITCH
        switch_in_progress = True
    elif phase == STAGE_EXECUTOR:
        if status is None or status in EXECUTOR_RUNNING_STATUSES:
            stage, stage_label = STAGE_EXECUTOR, LABEL_EXECUTOR_RUNNING
            executor_running = True
        else:
            # executor 阶段但 status 不在执行中词表（如 done）——透传 phase 并保留原始 status
            stage, stage_label = phase, "待识别: %s" % phase
    elif phase == STAGE_AUDITOR:
        if status is None or status in AUDITOR_REVIEWING_STATUSES:
            stage, stage_label = STAGE_AUDITOR, LABEL_AUDITOR_REVIEWING
            auditor_reviewing = True
        else:
            stage, stage_label = phase, "待识别: %s" % phase
    elif phase is not None:
        # 其它阶段（planning 等）：透传，不发明语义
        stage, stage_label = phase, "待识别: %s" % phase
    else:
        stage, stage_label = STAGE_UNKNOWN, LABEL_UNKNOWN

    return {
        "run_id": run_id,
        "index": run.get("index"),
        "phase": phase,
        "status": status,
        "module": module,
        "updated_at": updated_at,
        "stage": stage,
        "stage_label": stage_label,
        "executor_running": executor_running,
        "auditor_reviewing": auditor_reviewing,
        "switch_in_progress": switch_in_progress,
        "needs_human": needs_human,
        "module_states": module_states,
    }


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def compute(raw: Any) -> Dict[str, Any]:
    """fwr.status.compute 主实现：由 m01 原始数据计算各 run 的阶段/模块状态。

    确定性、永不抛异常：
      - 输入不可用 / m01 ok=False → 确定性空结果（ok=False）
      - 有效输入 runs 为空 → ok=True, runs=[]
      - 单条 run 快照异常 → 跳过并记 errors
    """
    if not isinstance(raw, dict):
        return empty_result(None)
    if raw.get("ok") is False:
        return empty_result(raw.get("task_dir"))
    runs_raw = raw.get("runs")
    if not isinstance(runs_raw, list):
        return empty_result(raw.get("task_dir"))

    dispatch_events = raw.get("dispatch_events")
    errors: List[str] = []
    runs: List[Dict[str, Any]] = []
    for idx, run in enumerate(runs_raw):
        if not isinstance(run, dict):
            errors.append("runs[%d]: 非 dict，已跳过" % idx)
            continue
        try:
            runs.append(compute_run(run, dispatch_events))
        except Exception as exc:  # 防御性兜底：单条失败不拖垮整体（正常路径不应触发）
            errors.append("runs[%d]: 计算失败已跳过（%s）" % (idx, exc))

    return {
        "ok": True,
        "task_dir": str(raw.get("task_dir")) if raw.get("task_dir") is not None else None,
        "runs": runs,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# fwr.status.compute 命名空间（对齐契约 path）
# ---------------------------------------------------------------------------

class _status:
    """fwr.status 命名空间：fwr.status.compute(raw)。"""

    compute = staticmethod(compute)


fwr = SimpleNamespace(status=_status)

__all__ = ["compute", "compute_run", "fwr", "empty_result"]
