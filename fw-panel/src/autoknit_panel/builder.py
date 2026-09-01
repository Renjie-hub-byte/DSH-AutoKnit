"""builder —— 面向「从路径/默认存储」的入口：读快照+事件流 → 面板状态。

供 dsh.panel.state 推送的服务端侧调用；默认路径解析见 paths.py（env + 任务根）。
"""

from .paths import snapshot_path as _snapshot_path, dispatch_path as _dispatch_path
from .paths import human_pending_path as _human_pending_path
from .snapshot import load_snapshot
from .events import load_events
from .state import build_panel_state


def build_from_paths(
    task_root=None,
    snapshot_path=None,
    dispatch_path=None,
    pending_path=None,
    require_snapshot=True,
):
    """读快照 + 事件流，拼面板状态。

    Args:
        task_root: 任务根目录（默认 env TASK_ROOT）。
        snapshot_path/dispatch_path/pending_path: 显式覆盖存储路径。
        require_snapshot: 快照缺失是否抛错。False 时缺快照返回空状态骨架。

    Returns:
        dict：dsh.panel.state 的 response 载荷（stage/roles/consumption/pending/progress）。
    """
    snap_path = snapshot_path or _snapshot_path(task_root)
    ev_path = dispatch_path or _dispatch_path(task_root)
    pend_path = pending_path if pending_path is not None else _human_pending_path(task_root)

    try:
        snapshot = load_snapshot(snap_path)
    except FileNotFoundError:
        if require_snapshot:
            raise
        snapshot = None

    events = load_events(ev_path)
    return build_panel_state(snapshot, events, pending_path=pend_path)
