"""fwr_detail.detail —— dsh.task.detail 查询服务（m04 的 dsh.task.detail 实现）。

契约（contracts/api.yaml）：path=dsh.task.detail, method=send
  —— F→R 拉取单个 run 的模块级状态（executor 执行中 / auditor 验收中 / 打回 N 次 / 换人中）。

输入：task_dir（fw-runner 任务目录绝对路径）+ run_id（run 标识）。
    - task_dir 由 m01 `fwr.dir.read` 解析（snapshot.json / dispatch.jsonl / modules/*/tmp）
    - run 阶段/模块状态由 m02b `fwr.status.compute` 计算（executor 执行中 / auditor 验收中 /
      switch 换人中 / needs_human，见上游 m02b docstring）
    - （打回次数 N 由 m02a `fwr.status.count_rejects` 统计 —— 下一轮接入，本轮不含）

输出：确定性结构化 dict（字段固定，无时间戳等随机量，同一输入多次调用结果精确相等）：
    {ok, task_dir, run_id, found, task_name, run: RunDetail|None, reason, errors}
    RunDetail（本轮字段，透传 m02b 计算结果 + 任务元信息）：
      run_id             run 标识（透传）
      index              原始快照序号（透传）
      phase              原始阶段（透传，如 executor/auditor）
      status             原始状态（透传，如 running/reviewing）
      module             当前模块（透传；缺省取最近一次派发事件的 module）
      updated_at         快照更新时间（透传，可缺省）
      stage              计算出的 DSH 阶段键：executor | auditor | switch | needs_human | …
      stage_label        中文标签：'executor 执行中' / 'auditor 验收中' / …
      executor_running   是否为 executor 执行中（bool）
      auditor_reviewing  是否为 auditor 验收中（bool）
      switch_in_progress 是否为换人中（bool，上游 m02b 计算透传）
      needs_human        是否需要人工介入（bool，上游 m02b 计算透传）
      module_states      模块级状态表（snapshot.modules dict 透传；无则 None）

本轮范围（objective 第一步）：executor 执行中 与 auditor 验收中（含确定性空结果路径）。
打回 N 次（m02a 接入）、换人中专项断言为下一轮待办——本轮对上游计算出的 switch/needs_human
字段仅原样透传，不发明语义、不返回假值字段。

降级规则（确定性，不抛异常）：
  - task_dir 无效（目录不存在 / task.yaml 缺失，m01 ok=False）→ 空结果 reason="task_dir_unavailable"
  - run_id 未命中任何 run（含非字符串/空）→ 空结果 reason="run_not_found"
  - 上游包（fwr_dir / fwr_status）不可导入 → 空结果 reason="upstream_unavailable"
  - 空结果结构固定：ok=False, found=False, run=None, errors=[]（与 m01/m02 空降级风格一致）
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# 上游依赖（m01 fwr_dir / m02b fwr_status）
# 说明：m02/m02a/m02b 都提供名为 fwr_status 的包（各自线），本模块需要的是 m02b 的
# fwr.status.compute（含 executor/auditor/switch/needs_human）；测试 conftest 注入
# 上游 src 到 sys.path（对齐 m02b test_integration_m01.py 的做法）。包不可导入时
# 服务降级为空结果（reason="upstream_unavailable"），不抛异常、不阻塞本模块单测。
# ---------------------------------------------------------------------------

try:  # m01：任务目录解析
    from . import fwr_dir  # type: ignore
except Exception:  # pragma: no cover
    fwr_dir = None

try:  # m02b：阶段与模块状态计算（含 switch/needs_human）
    from . import fwr_status  # type: ignore
except Exception:  # pragma: no cover
    fwr_status = None


# ---------------------------------------------------------------------------
# 确定性空结果
# ---------------------------------------------------------------------------

def empty_result(
    task_dir: Any = None,
    run_id: Any = None,
    reason: str = "run_not_found",
    errors: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """确定性空结果：run 不存在 / 任务目录不可用 / 上游不可用时返回。

    结构固定、不含随机量；同一输入多次调用结果可精确相等。errors 默认为空
    （空降级本身不是错误，与 m01/m02 空结果语义一致）。
    """
    return {
        "ok": False,
        "task_dir": str(task_dir) if task_dir is not None else None,
        "run_id": str(run_id) if run_id is not None else None,
        "found": False,
        "run": None,
        "reason": reason,
        "errors": list(errors) if errors else [],
    }


# ---------------------------------------------------------------------------
# 从 m01 原始数据构造单 run 详情（供 detail() 与下游 m06 复用）
# ---------------------------------------------------------------------------

def _build_detail(
    raw: Dict[str, Any],
    computed_run: Dict[str, Any],
    run_id: str,
) -> Dict[str, Any]:
    """把计算后的 RunStatus 组装为 dsh.task.detail 输出（确定性字段顺序）。"""
    task = raw.get("task")
    task_name = None
    if isinstance(task, dict) and isinstance(task.get("name"), str):
        task_name = task["name"].strip() or None
    return {
        "ok": True,
        "task_dir": str(raw["task_dir"]) if raw.get("task_dir") is not None else None,
        "run_id": str(computed_run.get("run_id")) if computed_run.get("run_id") is not None else run_id,
        "found": True,
        "task_name": task_name,
        "run": computed_run,
        "errors": [],
    }


def detail_from_raw(raw: Any, run_id: Any) -> Dict[str, Any]:
    """由 m01 fwr.dir.read 的结构化原始数据 dict 拉取单个 run 的模块级状态。

    供 detail(task_dir, run_id) 内部使用；下游（如 m06 数据源注册）可复用：
    自行 read 任务目录后直接调本函数，避免重复解析。

    降级（确定性，不抛异常）：
      - raw 非 dict / m01 ok=False → 空结果 reason="task_dir_unavailable"
      - fwr_status 不可用 → 空结果 reason="upstream_unavailable"
      - run_id 未命中任何 run → 空结果 reason="run_not_found"
    """
    if not isinstance(raw, dict):
        return empty_result(None, run_id, reason="task_dir_unavailable")
    task_dir = raw.get("task_dir")
    if raw.get("ok") is False:
        return empty_result(task_dir, run_id, reason="task_dir_unavailable")

    run_id_str: str = str(run_id) if run_id is not None else ""
    if not run_id_str.strip():
        return empty_result(task_dir, run_id, reason="run_not_found")

    if fwr_status is None:
        return empty_result(
            task_dir, run_id, reason="upstream_unavailable",
            errors=["上游 fwr_status（m02b）不可导入，无法计算 run 状态"],
        )

    # fwr.status.compute：确定性、不抛异常（上游契约）
    computed = fwr_status.compute(raw)
    if computed.get("ok") is False:
        # raw 有效但 compute 降级（极端防御路径，正常不应触发）
        return empty_result(task_dir, run_id, reason="run_not_found")

    for run in computed.get("runs", []):
        if not isinstance(run, dict):
            continue
        if run.get("run_id") == run_id_str:
            return _build_detail(raw, run, run_id_str)
    return empty_result(task_dir, run_id, reason="run_not_found")


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def detail(task_dir: Any, run_id: Any) -> Dict[str, Any]:
    """dsh.task.detail 主实现：拉取指定 run 的模块级状态。

    流程：fwr_dir.read(task_dir) → m01 原始数据 → fwr_status.compute → 按 run_id
    定位 → 返回单 run 详情。全程只读、确定性、不抛异常（见模块 docstring 降级规则）。
    """
    run_id_str: str = str(run_id) if run_id is not None else ""
    if not run_id_str.strip():
        return empty_result(task_dir, run_id, reason="run_not_found")

    if fwr_dir is None:
        return empty_result(
            task_dir, run_id, reason="upstream_unavailable",
            errors=["上游 fwr_dir（m01）不可导入，无法解析任务目录"],
        )

    # task_dir 非路径类型（None/int 等）直接空降级——上游 m01 os.fspath 不接受
    # 非路径对象（如 None 会抛 TypeError），本服务保证在任何输入下都不抛异常。
    if not isinstance(task_dir, (str, os.PathLike)):
        return empty_result(task_dir, run_id, reason="task_dir_unavailable")

    raw = fwr_dir.read(task_dir)  # 确定性、不抛异常（m01 契约）
    if raw.get("ok") is False:
        return empty_result(task_dir, run_id, reason="task_dir_unavailable")

    return detail_from_raw(raw, run_id)


# ---------------------------------------------------------------------------
# dsh.task.detail 命名空间（对齐契约 path：dsh.task.detail）
# ---------------------------------------------------------------------------

class _task:
    """dsh.task 命名空间：dsh.task.detail(task_dir, run_id)。"""

    detail = staticmethod(detail)


dsh = SimpleNamespace(task=_task)

__all__ = ["detail", "detail_from_raw", "empty_result", "dsh"]
