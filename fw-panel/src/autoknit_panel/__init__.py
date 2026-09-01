"""autoknit_panel —— AutoKnit 运行状态读取与面板状态拼装（服务端侧）。

职责（对齐数据契约 data_contract）：
  * 读 总日志/快照.json（snapshot_store）与事件流 dispatch.jsonl；
  * 拼进度（模块完成情况）；
  * 读 human_pending（needs_human / human_pending 文件）；
  * 拼当前阶段与各角色 token 消耗（代码级，供 dsh.panel.state 推送）。

本模块是 dsh-client-ui-autoknit 面板的服务端数据源。纯标准库实现，不调 LLM，
不写文件（只读快照/事件/待决策）。交互写盘（human_answer.json）属后续轮次。
"""

from .enums import (
    STAGES,
    ROLES,
    HUMAN_CHOICES,
    DEFAULT_STAGE,
    normalize_stage,
    normalize_roles,
)
from .snapshot import Snapshot, load_snapshot, parse_snapshot
from .events import Event, load_events, parse_event_line, role_for_event, latest_role
from .progress import build_progress
from .pending import read_human_pending
from .consumption import build_consumption
from .state import build_panel_state, derive_stage
from .builder import build_from_paths

__all__ = [
    "STAGES",
    "ROLES",
    "HUMAN_CHOICES",
    "DEFAULT_STAGE",
    "normalize_stage",
    "normalize_roles",
    "Snapshot",
    "load_snapshot",
    "parse_snapshot",
    "Event",
    "load_events",
    "parse_event_line",
    "role_for_event",
    "latest_role",
    "build_progress",
    "read_human_pending",
    "build_consumption",
    "build_panel_state",
    "derive_stage",
    "build_from_paths",
]
