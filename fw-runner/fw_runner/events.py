"""事件日志（总日志/dispatch.jsonl）—— 事件 seq 完整性（dsh 事件流本地形态）。

- 每行一个 JSON 事件，seq 单调递增（同一次运行内不重复；resume 时从快照 last_seq 续号）
- run_id 标识一次运行（每次 run 唯一；resume 续跑沿用同一 run_id，保证事件流连续性）
- 单一写者串行 emit（runner 主循环），O_APPEND 追加；严格一致性场景可用原子重写
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .io_utils import atomic_append_jsonl


# v1.0 新增事件类型（G1；与 runner 实际 emit 的事件名对齐）
EVENT_MODULE_SPLIT = "module.split"                  # 模块被拆分
EVENT_MODULE_SPLIT_FAILED = "module.split_failed"    # 拆分失败（回人，不硬拆）
EVENT_MODULE_AGGREGATED = "module.aggregated"        # 父模块聚合收敛 done
EVENT_MODULE_MERGE_BACK = "module.merge_back"        # 子模块合并回父
EVENT_MODULE_MODEL_UPGRADE = "module.model_upgrade"  # 叶子模块升级模型（pro 兜底）
EVENT_MODULE_HUMAN_ABANDONED = "module.human_abandoned"  # 人工决策：放弃该模块
EVENT_MODULE_HUMAN_RERUN = "module.human_rerun"          # 人工决策：改方案/自定义后重跑

V1_EVENT_TYPES = (
    EVENT_MODULE_SPLIT,
    EVENT_MODULE_SPLIT_FAILED,
    EVENT_MODULE_AGGREGATED,
    EVENT_MODULE_MERGE_BACK,
    EVENT_MODULE_MODEL_UPGRADE,
    EVENT_MODULE_HUMAN_ABANDONED,
    EVENT_MODULE_HUMAN_RERUN,
)


class EventLog:
    """dispatch.jsonl 事件日志（seq 单调递增，线程安全——批次内并行模块并发 emit）。

    并发安全：自增 + 拼装 + 追加在同一把锁内完成，保证文件内 seq 严格单调不重复
    （事件 seq 完整性；对应 dsh 事件流能力）。
    """

    def __init__(self, path: str | Path, run_id: str, start_seq: int = 0) -> None:
        self.path = Path(path)
        self.run_id = run_id
        self._seq = int(start_seq)
        self._lock = threading.Lock()

    @property
    def last_seq(self) -> int:
        with self._lock:
            return self._seq

    def emit(self, event: str, module: Optional[str] = None,
             action: Optional[str] = None, detail: Optional[Dict[str, Any]] = None) -> int:
        """追加一个事件，返回其 seq（并发安全）。"""
        with self._lock:
            self._seq += 1
            seq = self._seq
            record = {
                "seq": seq,
                "ts": _ts(),
                "run_id": self.run_id,
                "event": event,
                "module": module,
                "action": action,
                "detail": detail or {},
            }
            atomic_append_jsonl(self.path, record)
        return seq

    def read_all(self) -> list[Dict[str, Any]]:
        if not self.path.exists():
            return []
        out = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out


def _ts() -> str:
    import datetime as _dt
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def existing_run_ids(path: str | Path) -> List[str]:
    """读取 jsonl 中出现的全部 run_id（无 run_id 的 scaffold 标记行忽略）。

    用途：同一任务根从零重新 run 时，旧 run_id 的事件流需归档，
    保证活动 dispatch.jsonl 内 seq 域干净（事件完整性按 run 域保证）。
    """
    ids: List[str] = []
    seen = set()
    if not Path(path).is_file():
        return ids
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rid = rec.get("run_id")
                if isinstance(rid, str) and rid and rid not in seen:
                    seen.add(rid)
                    ids.append(rid)
    except OSError:
        pass
    return ids


def new_run_id() -> str:
    import datetime as _dt
    import uuid
    return f"run-{_dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
