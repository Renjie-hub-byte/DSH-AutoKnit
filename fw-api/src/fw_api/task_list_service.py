"""dsh_task_list.service —— dsh.task.list 查询服务（m03 的 fwr.task.list 实现）。

契约（contracts/api.yaml）：path=dsh.task.list, method=send
  —— F→R 拉取按紧急度排序的任务列表（含阶段/模块状态）。

输入：任务目录绝对路径（dsh.task.list(task_dir)）；内部链路：
    m01 `fwr_dir.read(task_dir)` → 结构化原始数据 dict
    → m02 `fwr_status.compute(raw)` → 各 run 阶段/模块状态
    → 组装任务数组 + 按紧急度排序

输出：确定性结构化 dict（字段执行期涌现，见 contract.yaml 回填）：
    {ok, task_dir, tasks: [TaskEntry...], task_count, errors}
    TaskEntry（每条含阶段状态与模块状态，透传 m02 RunStatus 并附加）：
      run_id / index / phase / status / module / updated_at   —— 原始信息透传
      stage / stage_label                                     —— 计算阶段键与中文标签
      executor_running / auditor_reviewing                    —— 阶段布尔标志
      switch_in_progress / needs_human                        —— m02 有则透传（无则缺省）
      module_states          —— 模块级状态表（模块状态核心字段，无则 None）
      task_name              —— task.yaml 的任务名（仅完整链路注入，可缺省）
      urgency                —— 紧急度分值（0 最紧急，数值越小越靠前）

紧急度排序规则（确定性，执行期涌现）：
    needs_human(0) > switch(1) > auditor(2) > executor(3) > 其它透传阶段(4) > unknown(5)
    语义：阻塞/需人工介入 > 换人过渡 > 待验收 > 执行中 > 其它 > 未知。
    同紧急度按 index（原始快照序号）升序 —— 同输入多次调用结果精确相等，无随机量。

降级规则（确定性，永不抛异常）：
  - 任务目录不存在 / task.yaml 缺失（m01 ok=False）→ ok=False, tasks=[]
  - 目录有效但无活跃 run（runs 为空）→ ok=True, tasks=[]（无活跃 run 空降级）
  - 上游 fwr_dir/fwr_status 不可导入（上游未挂载）→ ok=False, tasks=[], errors 记
  - 单条 run 状态异常 → 跳过该条并记 errors（m02 已保证，此处仅防御）
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# 紧急度分值：数值越小越紧急（0 最紧急）
# ---------------------------------------------------------------------------

URGENCY_RANK = {
    "needs_human": 0,   # 需人工介入（阻塞等待，最紧急）
    "switch": 1,        # 换人中（executor 切换过渡中）
    "auditor": 2,       # auditor 验收中（待验收结论）
    "executor": 3,      # executor 执行中（工作推进中）
}
URGENCY_OTHER = 4       # 其它透传阶段（planning 等，未定义紧急语义）
URGENCY_UNKNOWN = 5     # unknown / 缺 stage / 非字符串

# ---------------------------------------------------------------------------
# 确定性空结果
# ---------------------------------------------------------------------------


def empty_result(task_dir: Any = None) -> Dict[str, Any]:
    """确定性空降级结果：目录缺失/上游不可用/输入不可用时返回。

    结构固定、不含时间戳等随机量，同一输入多次调用结果可精确相等。
    """
    return {
        "ok": False,
        "task_dir": str(task_dir) if task_dir is not None else None,
        "tasks": [],
        "task_count": 0,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 紧急度计算
# ---------------------------------------------------------------------------


def urgency_of(stage: Any) -> int:
    """计算 stage 的紧急度分值；None/非字符串/unknown → URGENCY_UNKNOWN。

    确定性：同一 stage 恒返回同一分值。
    """
    if isinstance(stage, str):
        if stage in URGENCY_RANK:
            return URGENCY_RANK[stage]
        if stage == "unknown":
            return URGENCY_UNKNOWN
        return URGENCY_OTHER
    return URGENCY_UNKNOWN


def _index_key(entry: Dict[str, Any]) -> Any:
    """同紧急度内的次排序键：index（原始快照序号）升序；缺 index 排最后。"""
    idx = entry.get("index")
    if isinstance(idx, int):
        return idx
    return 10 ** 9  # 缺 index 视为无穷大，保证确定性


# ---------------------------------------------------------------------------
# 组装 + 排序（纯函数，输入为 m02 fwr.status.compute 的结果）
# ---------------------------------------------------------------------------


def assemble(status_result: Any) -> Dict[str, Any]:
    """由 m02 `fwr.status.compute` 的结果组装按紧急度排序的任务数组。

    输入形态（对齐 m02 contract.yaml）：
        {ok, task_dir, runs: [RunStatus...], errors}
    输出：{ok, task_dir, tasks, task_count, errors}；每条 run 一个任务条目，
    透传 m02 RunStatus 全部字段并附加 urgency。

    降级（确定性，不抛异常）：
      - 输入为 None / 非 dict / ok=False / 缺 runs → 确定性空结果（ok=False, tasks=[]）
      - 单条 run 状态非 dict 或组装异常 → 跳过该条并记 errors
    """
    if not isinstance(status_result, dict):
        return empty_result(None)
    if status_result.get("ok") is False:
        return empty_result(status_result.get("task_dir"))
    runs = status_result.get("runs")
    if not isinstance(runs, list):
        return empty_result(status_result.get("task_dir"))

    errors: List[str] = list(status_result.get("errors") or [])
    tasks: List[Dict[str, Any]] = []
    for idx, run in enumerate(runs):
        if not isinstance(run, dict):
            errors.append("tasks[%d]: run 状态非 dict，已跳过" % idx)
            continue
        try:
            entry = dict(run)  # 透传 m02 RunStatus 全部字段（阶段状态 + 模块状态）
            entry["urgency"] = urgency_of(run.get("stage"))
            tasks.append(entry)
        except Exception as exc:  # 防御性兜底：单条失败不拖垮整体（正常路径不应触发）
            errors.append("tasks[%d]: 组装失败已跳过（%s）" % (idx, exc))

    # 确定性排序：紧急度升序（0 最紧急），同紧急度按 index 升序（原始快照序）
    tasks.sort(key=lambda t: (t.get("urgency", URGENCY_UNKNOWN), _index_key(t)))

    return {
        "ok": True,
        "task_dir": (
            str(status_result.get("task_dir"))
            if status_result.get("task_dir") is not None
            else None
        ),
        "tasks": tasks,
        "task_count": len(tasks),
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# 完整链路（懒加载上游，未挂载时确定性降级）
# ---------------------------------------------------------------------------


def _inject_task_name(result: Dict[str, Any], raw: Any) -> Dict[str, Any]:
    """把 task.yaml 的任务名注入每条任务条目（确定性；task 缺失/无名则跳过）。"""
    if not result.get("ok"):
        return result
    name = None
    task = raw.get("task") if isinstance(raw, dict) else None
    if isinstance(task, dict):
        n = task.get("name")
        if isinstance(n, str) and n.strip():
            name = n.strip()
    if name is not None:
        for entry in result.get("tasks") or []:
            entry["task_name"] = name
    return result


def list_from_status(status_result: Any) -> Dict[str, Any]:
    """从已计算好的 m02 状态结果直接出任务列表（供已算好状态的调用方使用）。"""
    return assemble(status_result)


def list_tasks(task_dir: Any) -> Dict[str, Any]:
    """dsh.task.list 主实现：任务目录 → m01 读原始 → m02 算状态 → 组装排序。

    只读数据桥：不写任何任务状态文件；任何一步失败都走确定性空降级，永不抛异常。
    """
    task_dir_str = str(task_dir) if task_dir is not None else None
    try:  # 懒加载上游 m01/m02：未挂载时降级，不阻塞本包导入
        from . import fwr_dir, fwr_status
    except Exception as exc:
        result = empty_result(task_dir_str)
        result["errors"].append("上游模块不可导入（fwr_dir/fwr_status）：%s" % exc)
        return result

    try:
        raw = fwr_dir.read(task_dir)
    except Exception as exc:  # 防御：m01 正常不抛，此处兜底
        result = empty_result(task_dir_str)
        result["errors"].append("fwr_dir.read 异常：%s" % exc)
        return result

    status_result = fwr_status.compute(raw)
    return _inject_task_name(assemble(status_result), raw)


# ---------------------------------------------------------------------------
# dsh.task.list 命名空间（对齐契约 path）
# ---------------------------------------------------------------------------


class _task:
    """dsh.task 命名空间：dsh.task.list(task_dir)。"""

    list = staticmethod(list_tasks)


dsh = SimpleNamespace(task=_task)

__all__ = [
    "list_tasks",
    "list_from_status",
    "assemble",
    "empty_result",
    "urgency_of",
    "URGENCY_RANK",
    "dsh",
]
