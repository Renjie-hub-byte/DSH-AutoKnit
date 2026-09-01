"""dsh_update.watcher —— 文件变化事件 → dsh.task.update 广播触发（m05 核心链路）。

事件驱动、非轮询：本模块不做任何 sleep/轮询。广播由外部（文件系统监听器或测试
注入）产生"文件变化事件"（{path, kind}）→ `TaskUpdateWatcher.handle(task_dir,
fs_events)` 逐条处理 → 命中关注文件（snapshot.json / dispatch.jsonl /
modules/*/tmp）且存在活跃 run → 构造广播事件 → `bus.push()`。

本轮范围（objective 第一步，最小链路）：
  - 文件变化事件注入 → 广播触发的最小链路（可单测，测试不依赖轮询 sleep）；
  - 三类变化类型（阶段变更/模块变更/needs_human 出现）的**启发式分类**：
      snapshot.json 变化且该 run 计算为 needs_human → change_type="needs_human"；
      snapshot.json 其它变化                          → change_type="stage"；
      dispatch.jsonl / modules/*/tmp 变化             → change_type="module"；
    （精确的 before/after diff 分类为下一轮待办，见交付说明.md 交接备注）

守卫规则（确定性，不误触发）：
  - 任务目录缺失 / task.yaml 缺失（m01 ok=False）→ 不广播；
  - 无活跃 run（快照缺失/为空，或所有 run 均不在
    executor_running/auditor_reviewing/switch_in_progress/needs_human）→ 不广播；
  - 事件路径不在关注文件集合（含 task_dir 之外、非 tmp 的 modules 路径）→ 不广播；
  - 上游包（fwr_dir / fwr_status）不可导入 → 确定性不广播（不抛异常）。

边界：不写任何任务状态文件（watcher 只读 m01 数据桥）；不声明/实现
dsh.task.list 与 dsh.task.detail。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .broadcast import API_PATH, UpdateBus

# ---------------------------------------------------------------------------
# 上游依赖（m01 fwr_dir / m02b fwr_status）
# 说明：m02/m02a/m02b 都提供名为 fwr_status 的包（各自线），本模块需要的是
# m02b 的 fwr.status.compute（含 switch_in_progress / needs_human 字段，供活跃
# run 判定与 needs_human 分类）；测试 conftest 注入上游 src 到 sys.path。
# 包不可导入时 watcher 确定性不广播（不抛异常、不阻塞本模块单测）。
# ---------------------------------------------------------------------------

try:  # m01：任务目录解析
    import fwr_dir  # type: ignore
except Exception:  # pragma: no cover
    fwr_dir = None

try:  # m02b：阶段与模块状态计算（含 switch/needs_human）
    import fwr_status  # type: ignore
except Exception:  # pragma: no cover
    fwr_status = None

# ---------------------------------------------------------------------------
# 常量：关注文件源 / 变化类型
# ---------------------------------------------------------------------------

SOURCE_SNAPSHOT = "snapshot.json"
SOURCE_DISPATCH = "dispatch.jsonl"
SOURCE_MODULES = "modules"        # modules/<id>/tmp 下任何变化
SOURCE_OTHER = "other"            # 不在关注集合 → 不触发

CHANGE_STAGE = "stage"            # 阶段变更（snapshot.json 变化，非 needs_human）
CHANGE_MODULE = "module"          # 模块变更（dispatch.jsonl / modules/*/tmp 变化）
CHANGE_NEEDS_HUMAN = "needs_human"  # needs_human 出现（snapshot.json 变化且 run 判定 needs_human）


def normalize_event_path(task_dir: Any, path: Any) -> Optional[str]:
    """把事件 path 规范化为相对 task_dir 的 posix 路径。

    - path 为绝对路径且位于 task_dir 内 → 相对路径；
    - path 为相对路径 → 视为相对 task_dir；
    - path 位于 task_dir 之外 / 非法 → None（不触发）。
    规范化不要求路径真实存在（事件可能来自删除）。
    """
    if path is None:
        return None
    try:
        base = Path(os.fspath(task_dir))
        p = Path(os.fspath(path))
        if not p.is_absolute():
            p = base / p
        rel = p.resolve().relative_to(base.resolve())
        return rel.as_posix()
    except Exception:
        return None


def map_source(rel: str) -> str:
    """把相对路径映射到关注文件源：snapshot.json / dispatch.jsonl / modules / other。"""
    if rel == SOURCE_SNAPSHOT:
        return SOURCE_SNAPSHOT
    if rel == SOURCE_DISPATCH:
        return SOURCE_DISPATCH
    if rel.startswith("modules/"):
        # 只关注 modules/<id>/tmp 及其下内容；modules/ 其它路径（如 modules/<id>/交付说明.md）不触发
        parts = rel.split("/")
        if len(parts) >= 3 and parts[1] and parts[2] == "tmp":
            return SOURCE_MODULES
    return SOURCE_OTHER


def _active_run(run: Dict[str, Any]) -> bool:
    """活跃 run 判定：executor 执行中 / auditor 验收中 / 换人中 / needs_human 任一命中。

    与 objective 三类广播触发（阶段变更/模块变更/needs_human 出现）对齐——
    仅这些状态下的 run 才值得广播；已完成（status=done 等）或未知阶段不广播。
    """
    return bool(
        run.get("executor_running")
        or run.get("auditor_reviewing")
        or run.get("switch_in_progress")
        or run.get("needs_human")
    )


class TaskUpdateWatcher:
    """文件变化事件 → dsh.task.update 广播的最小链路执行器。

    用法：
        watcher = TaskUpdateWatcher()
        pushed = watcher.handle(task_dir, [{"path": ".../snapshot.json", "kind": "modified"}])
    handle() 返回实际推送给订阅者的广播事件列表（供测试断言；也为框架提供
    可观测的触发结果）。同一输入多次调用结果精确相等（确定性）。
    """

    def __init__(self, bus: Optional[UpdateBus] = None) -> None:
        self.bus = bus if bus is not None else UpdateBus()
        self.errors: List[str] = []

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def handle(self, task_dir: Any, fs_events: Any) -> List[Dict[str, Any]]:
        """处理注入的文件变化事件（事件驱动，非轮询），返回推送的广播事件列表。

        流程：
          1) 任务目录有效性守卫：m01 fwr.dir.read → ok=False（目录缺失/task.yaml
             缺失/上游不可用）→ 确定性返回 []；
          2) 活跃 run 守卫：m02b fwr.status.compute → 无活跃 run → 返回 []；
          3) 逐事件处理：路径规范化 → 源映射 → 非关注源跳过 → 对每个活跃 run
             构造广播事件 → bus.push()，并收集返回。
        """
        pushed: List[Dict[str, Any]] = []
        if not isinstance(fs_events, list):
            return pushed

        # 上游不可用：确定性不广播（不抛异常，语义同 m04 upstream_unavailable 空降级）
        if fwr_dir is None or fwr_status is None:
            self.errors.append("上游 fwr_dir(m01)/fwr_status(m02b) 不可导入，本次不广播")
            return pushed

        # 1) 任务目录有效性守卫（m01 契约：目录缺失/task.yaml 缺失 → ok=False）
        raw = fwr_dir.read(task_dir)
        if raw.get("ok") is False:
            return pushed

        # 2) 活跃 run 守卫
        computed = fwr_status.compute(raw)
        active_runs = [
            r for r in computed.get("runs", [])
            if isinstance(r, dict) and _active_run(r)
        ]
        if not active_runs:
            return pushed

        # 3) 逐事件处理（一个事件命中关注源 → 对每个活跃 run 广播一条）
        for ev in fs_events:
            if not isinstance(ev, dict):
                continue
            rel = normalize_event_path(task_dir, ev.get("path"))
            if rel is None:
                continue
            source = map_source(rel)
            if source == SOURCE_OTHER:
                continue
            kind = ev.get("kind")
            if not isinstance(kind, str):
                kind = "modified"
            for run in active_runs:
                event = self._build_event(task_dir, source, kind, rel, run)
                self.bus.push(event)
                pushed.append(event)
        return pushed

    # ------------------------------------------------------------------
    # 事件构造
    # ------------------------------------------------------------------

    def _build_event(
        self,
        task_dir: Any,
        source: str,
        kind: str,
        rel: str,
        run: Dict[str, Any],
    ) -> Dict[str, Any]:
        """构造一条 dsh.task.update 广播事件（确定性字段顺序，不含随机量）。

        变化类型启发式（本轮）：
          - snapshot.json 变化且该 run 计算为 needs_human → needs_human 出现；
          - snapshot.json 其它变化 → 阶段变更；
          - dispatch.jsonl / modules/*/tmp 变化 → 模块变更。
        """
        if source == SOURCE_SNAPSHOT:
            change_type = CHANGE_NEEDS_HUMAN if run.get("needs_human") else CHANGE_STAGE
        else:
            change_type = CHANGE_MODULE
        return {
            "api": API_PATH,
            "change_type": change_type,
            "source": source,
            "kind": kind,
            "path": rel,
            "task_dir": str(task_dir),
            "run_id": run.get("run_id"),
            "stage": run.get("stage"),
            "stage_label": run.get("stage_label"),
            "phase": run.get("phase"),
            "status": run.get("status"),
            "module": run.get("module"),
        }


def create_watcher(bus: Optional[UpdateBus] = None) -> TaskUpdateWatcher:
    """便捷工厂：创建事件驱动 watcher（可注入共享总线）。"""
    return TaskUpdateWatcher(bus)


# ---------------------------------------------------------------------------
# 真实文件系统监听适配（可选，需要第三方库 watchdog）
# ---------------------------------------------------------------------------

def watch(
    task_dir: Any,
    watcher: Optional["TaskUpdateWatcher"] = None,
) -> Any:
    """把 OS 文件系统事件桥接到 dsh.task.update 广播（事件驱动、非轮询）。

    依赖：watchdog 库（macOS fsevents / Linux inotify 封装）。本环境未安装
    watchdog（见交付说明.md 已知风险），因此本适配器未在本模块测试中覆盖——
    调用方应自行 try/except ImportError 处理缺依赖；核心链路
    （watcher.handle 注入式事件驱动）已由 pytest 全量覆盖，不受影响。

    用法（安装 watchdog 后）：
        watcher = create_watcher()
        observer = dsh_update.watch(task_dir, watcher)   # 已 schedule
        observer.start()                                  # 开始监听（事件驱动）
        ...
        observer.stop(); observer.join()

    返回 watchdog Observer（已 schedule 未 start）。OS 事件（created/modified/
    deleted，递归含子目录）→ watcher.handle(task_dir, [{path, kind}...])。
    """
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError as exc:  # pragma: no cover - 环境缺依赖路径
        raise ImportError(
            "缺依赖 watchdog（用途：OS 文件系统事件监听适配，非核心链路；"
            "核心链路为注入式 watcher.handle()，无需该库）"
        ) from exc

    w = watcher if watcher is not None else TaskUpdateWatcher()
    base = os.fspath(task_dir)

    class _Handler(FileSystemEventHandler):  # type: ignore[misc]
        """把单条 OS 事件转成注入事件 → watcher.handle（事件驱动、非轮询）。"""

        def on_any_event(self, ev: Any) -> None:  # pragma: no cover - 依赖 watchdog，本环境未测
            if ev.event_type not in ("created", "modified", "deleted"):
                return
            w.handle(base, [{"path": ev.src_path, "kind": ev.event_type}])

    observer = Observer()
    observer.schedule(_Handler(), base, recursive=True)
    return observer


__all__ = [
    "TaskUpdateWatcher",
    "create_watcher",
    "normalize_event_path",
    "map_source",
    "watch",
    "SOURCE_SNAPSHOT",
    "SOURCE_DISPATCH",
    "SOURCE_MODULES",
    "CHANGE_STAGE",
    "CHANGE_MODULE",
    "CHANGE_NEEDS_HUMAN",
]
