"""dsh_update —— m05 事件驱动广播包（dsh.task.update push 实现）。

契约 path：dsh.task.update（method: push）—— R→F 任务状态变化广播
（阶段变更/模块变更/needs_human 出现），事件驱动、非轮询。

用法：
    import dsh_update
    bus = dsh_update.UpdateBus()                     # 独立总线（测试推荐）
    watcher = dsh_update.create_watcher(bus)         # 事件驱动链路执行器
    pushed = watcher.handle(task_dir, [{"path": ".../snapshot.json", "kind": "modified"}])
    # 订阅者收到广播：
    received = []
    bus.subscribe(received.append)
    # 或对齐契约命名（默认总线）：
    from dsh_update import dsh
    dsh.task.update.push(event)

只读事件链：不写任何任务状态文件；任务目录缺失/无活跃 run/上游不可用 →
确定性不广播（不抛异常）。真实 OS 监听适配（watch()）需要 watchdog 库，
核心链路不依赖它（测试注入事件驱动）。
"""

from .broadcast import API_PATH, UpdateBus, default_bus, dsh, push
from .watcher import (
    CHANGE_MODULE,
    CHANGE_NEEDS_HUMAN,
    CHANGE_STAGE,
    SOURCE_DISPATCH,
    SOURCE_MODULES,
    SOURCE_SNAPSHOT,
    TaskUpdateWatcher,
    create_watcher,
    map_source,
    normalize_event_path,
    watch,
)

__all__ = [
    "UpdateBus",
    "default_bus",
    "push",
    "dsh",
    "API_PATH",
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
__version__ = "0.1.0"
