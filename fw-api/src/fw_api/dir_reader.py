"""fwr_dir.reader —— fw-runner 任务目录只读解析器（m01 的 fwr.dir.read 实现）。

契约（contracts/api.yaml）：path=fwr.dir.read, method=send
  —— 读取并解析任务目录原始状态文件，供上层（状态计算/查询服务）调用。

本模块只读：绝不写任何任务状态文件（boundaries：只读数据桥）。
缺失/损坏一律不抛异常，返回确定性的结构化空降级结果。

输入：任务目录绝对路径，结构约定：
    <task_dir>/
      task.yaml            # 任务定义（有效性前提：缺失 → 整体空降级）
      snapshot.json        # run 状态快照（对象 / 数组 / {"runs": [...]}）
      dispatch.jsonl       # 派发事件日志（每行一个 JSON 事件）
      modules/<id>/tmp/    # 模块临时目录（模块清单来源）

输出：结构化 dict，字段固定（字段由执行期涌现后回填，见 contract.yaml）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

try:  # PyYAML 缺失时 task.yaml 降级为解析失败（记 errors，不抛异常）
    import yaml
except Exception:  # pragma: no cover
    yaml = None


# ---------------------------------------------------------------------------
# 确定性空结果
# ---------------------------------------------------------------------------

def empty_result(task_dir: Any) -> Dict[str, Any]:
    """确定性空降级结果：目录不存在或 task.yaml 缺失时返回。

    结构固定、不含时间戳等随机量，同一输入多次调用结果可精确相等。
    """
    return {
        "ok": False,
        "task_dir": str(task_dir),
        "task": None,
        "modules": [],
        "runs": [],
        "snapshot": None,
        "dispatch_events": [],
        "dispatch_count": 0,
        "missing": [],
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 低层读取（一律不抛异常）
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> Optional[Any]:
    """读 YAML；文件不存在/解析失败/为空 → None（不抛异常）。"""
    if yaml is None:
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return data
    except Exception:
        return None


def _load_json(path: Path) -> Optional[Any]:
    """读 JSON；文件不存在/解析失败 → None（不抛异常）。"""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _load_jsonl(path: Path) -> List[Any]:
    """读 JSONL；不存在的文件 → []，坏行降级为 parse_error 事件，不丢其他行。"""
    events: List[Any] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except Exception as exc:
                    events.append({"parse_error": str(exc), "line": line_no})
    except FileNotFoundError:
        return []
    except Exception:
        return []
    return events


# ---------------------------------------------------------------------------
# 模块 tmp 扫描
# ---------------------------------------------------------------------------

def _scan_modules(modules_dir: Path) -> List[Dict[str, Any]]:
    """扫描 modules/*/tmp 产出模块清单（确定性顺序：按模块 id 排序）。"""
    modules: List[Dict[str, Any]] = []
    if not modules_dir.is_dir():
        return modules
    for child in sorted(p for p in modules_dir.iterdir() if p.is_dir()):
        tmp = child / "tmp"
        has_tmp = tmp.is_dir()
        tmp_files: List[str] = []
        if has_tmp:
            for f in sorted(tmp.rglob("*")):
                if f.is_file():
                    tmp_files.append(str(f.relative_to(tmp)))
        modules.append(
            {
                "id": child.name,
                "path": str(child),
                "has_tmp": has_tmp,
                "tmp_dir": str(tmp) if has_tmp else None,
                "tmp_files": tmp_files,
                "tmp_file_count": len(tmp_files),
            }
        )
    return modules


# ---------------------------------------------------------------------------
# run 状态快照归一化
# ---------------------------------------------------------------------------

def _normalize_runs(snapshot: Any) -> List[Dict[str, Any]]:
    """把 snapshot.json 归一化为 run 列表：dict→单 run；list→逐项；
    {"runs": [...]}→内层列表。run_id 优先取 run_id/id/name，缺省 run-<index>。
    """
    if snapshot is None:
        return []
    items: List[Any]
    if isinstance(snapshot, list):
        items = snapshot
    elif isinstance(snapshot, dict) and isinstance(snapshot.get("runs"), list):
        items = snapshot["runs"]
    elif isinstance(snapshot, dict):
        items = [snapshot]
    else:
        return []

    runs: List[Dict[str, Any]] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            item = {"raw": item}
        run_id = (
            item.get("run_id")
            or item.get("id")
            or item.get("name")
            or "run-%d" % i
        )
        runs.append({"run_id": str(run_id), "index": i, "snapshot": item})
    return runs


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def read_dir(task_dir: Any) -> Dict[str, Any]:
    """fwr.dir.read 主实现：解析任务目录并返回结构化原始数据。

    降级规则（确定性，不抛异常）：
      - 任务目录不存在           → empty_result（missing 标注目录不存在）
      - task.yaml 缺失/不可解析  → empty_result（missing 标注 task.yaml）——
        task.yaml 是任务目录有效性的前提，缺失即整体空降级
      - snapshot.json/dispatch.jsonl/modules 缺失 → 对应字段为空并记 missing，
        其余照常解析；文件存在但损坏 → 记 errors，对应字段空
    """
    task_dir = os.fspath(task_dir)
    base = Path(task_dir)
    result = empty_result(base)
    result["missing"] = []

    if not base.is_dir():
        result["missing"] = ["<task_dir 不存在>"]
        return result

    # 1) task.yaml —— 有效性前提
    task_yaml = base / "task.yaml"
    if not task_yaml.is_file():
        result["missing"] = ["task.yaml"]
        return result
    task = _load_yaml(task_yaml)
    if task is None:
        result["errors"].append("task.yaml: 解析失败或为空")
        return result
    result["ok"] = True
    result["task"] = task

    # 2) 快照 —— v1.0 框架正式路径 总日志/快照.json；兼容历史 快照.json / snapshot.json
    snap_candidates = [base / "总日志" / "快照.json", base / "快照.json", base / "snapshot.json"]
    snap_path = next((p for p in snap_candidates if p.is_file()), None)
    snapshot: Optional[Any] = None
    if snap_path is not None:
        snapshot = _load_json(snap_path)
        if snapshot is None:
            result["errors"].append(f"{snap_path.name}: 解析失败")
        else:
            result["snapshot"] = snapshot
    else:
        result["missing"].append("总日志/快照.json 或 快照.json/snapshot.json")

    # 3) dispatch.jsonl —— v1.0 正式路径 总日志/dispatch.jsonl；兼容根目录
    dispatch_candidates = [base / "总日志" / "dispatch.jsonl", base / "dispatch.jsonl"]
    dispatch_path = next((p for p in dispatch_candidates if p.is_file()), None)
    if dispatch_path is not None:
        events = _load_jsonl(dispatch_path)
        result["dispatch_events"] = events
        result["dispatch_count"] = len(events)
        if events and any("parse_error" in e for e in events if isinstance(e, dict)):
            result["errors"].append("dispatch.jsonl: 存在无法解析的行（已降级为 parse_error 事件）")
    else:
        result["missing"].append("dispatch.jsonl")

    # 4) modules/*/tmp
    modules_dir = base / "modules"
    if modules_dir.is_dir():
        result["modules"] = _scan_modules(modules_dir)
    else:
        result["missing"].append("modules/")

    # 5) run 状态快照归一化
    result["runs"] = _normalize_runs(snapshot)

    return result


# ---------------------------------------------------------------------------
# fwr.dir.read 命名空间（对齐契约 path）
# ---------------------------------------------------------------------------

class _dir:
    """fwr.dir 命名空间：fwr.dir.read(task_dir)。"""

    read = staticmethod(read_dir)


fwr = SimpleNamespace(dir=_dir)
read = read_dir  # 顶层别名：fwr_dir.read(task_dir)

__all__ = ["read_dir", "read", "fwr", "empty_result"]
