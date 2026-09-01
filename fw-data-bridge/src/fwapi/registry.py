"""fwapi.registry —— run 注册表（跨模块共享存储 runs_registry）。

契约（data_contract stores[runs_registry]，全链路唯一事实源，禁止自定义表名/路径/格式）：
- 默认路径：``~/.autoknit/runs.json``，可用环境变量 ``AUTOKNIT_RUNS_REGISTRY`` 覆盖绝对路径。
- 每 record 字段：run_id / task_dir / task / status / started_at / updated_at。
- status 枚举：active / complete / archived；未知/缺失确定性标 ``unknown``（禁止编造）。
- ts 存储格式：ISO-8601 UTC（``YYYY-MM-DDTHH:MM:SS+00:00``）。
- 文件格式：``{"runs": [ ... ]}``；缺失/损坏确定性读为 ``[]``；写入幂等、原子（临时文件+rename）。

职责边界：
- 本模块是 runs_registry 的 reader（register 提供读 + 更新 status 的能力供后续 archive 使用）；
- 注册表的写入主体是 fw-run.sh（程序包装器登记段，非 LLM 角色）；本模块只按契约读写同一文件，
  绝不自行发明字段/枚举/路径。
- 纯标准库实现，无第三方运行时依赖；跨线程共享访问用进程内锁保护。
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# 契约枚举：run 状态（对齐 data_contract.shared_enums.run_status）。
RUN_STATUS: tuple = ("active", "complete", "archived")

# 注册表文件默认路径（~/.autoknit/runs.json，可被 AUTOKNIT_RUNS_REGISTRY 覆盖为绝对路径）。
REGISTRY_REL = os.path.join("~", ".autoknit", "runs.json")

# 环境变量覆盖键（对齐 data_contract stores[runs_registry].env_var）。
ENV_REGISTRY = "AUTOKNIT_RUNS_REGISTRY"

# 注册表文件顶层键（与任务目录内 runs.json 形态一致，便于下游按既有约定读取）。
_KEY = "runs"

# 记录合法字段集合（落盘/读盘时据此清洗，防止未知字段污染；扩展可在契约更新时追加）。
_RECORD_FIELDS: tuple = (
    "run_id",
    "task_dir",
    "task",
    "status",
    "started_at",
    "updated_at",
)

# 注册表读写为跨线程共享操作，用进程内锁保证并发安全（幂等读改写）。
_LOCK = threading.Lock()


# ============================================================ 时间戳工具 ====


def now_utc() -> str:
    """当前 UTC 时间的 ISO-8601 字符串（契约 ts 存储格式，末尾 +00:00）。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def parse_iso(ts: Any) -> float:
    """把 ISO-8601 时间串解析为 epoch 秒；非字符串/解析失败确定性回落 0.0。

    兼容末尾 ``Z`` 与各种时区偏移（+00:00 / +08:00 …）；缺失当作最小时间，用于排序兜底。
    """
    if not isinstance(ts, str) or not ts.strip():
        return 0.0
    norm = ts.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(norm).timestamp()
    except (ValueError, OverflowError, OSError):
        return 0.0


# ============================================================ 路径解析 ====


def resolve_registry_path() -> str:
    """解析注册表文件绝对路径：优先环境变量覆盖，否则 ``~/.autoknit/runs.json``。"""
    env_path = os.environ.get(ENV_REGISTRY, "").strip()
    if env_path:
        return os.path.abspath(os.path.expanduser(env_path))
    return os.path.abspath(os.path.expanduser(REGISTRY_REL))


# ============================================================ 归一化 ====


def _coerce_str(raw: Any, default: str = "") -> str:
    """值归一化为字符串；非字符串确定性回落 default。"""
    return raw if isinstance(raw, str) else default


def _coerce_status(raw: Any) -> str:
    """status 归一化到契约枚举；未知值确定性标 'unknown'（禁止编造）。"""
    if raw in RUN_STATUS:
        return raw
    return "unknown"


def normalize_record(raw: Any) -> Optional[Dict[str, Any]]:
    """把单个 record 归一化为契约字段集合；缺 run_id 返回 None。

    未知字段被丢弃（仅保留契约字段，为后续契约扩展留口：新增字段须同步 _RECORD_FIELDS）。
    """
    if not isinstance(raw, dict):
        return None
    run_id = _coerce_str(raw.get("run_id"))
    if not run_id:
        return None
    return {
        "run_id": run_id,
        "task_dir": _coerce_str(raw.get("task_dir")),
        "task": _coerce_str(raw.get("task")),
        "status": _coerce_status(raw.get("status")),
        "started_at": _coerce_str(raw.get("started_at")),
        "updated_at": _coerce_str(raw.get("updated_at")),
    }


# ============================================================ 读 ====


def read_records() -> List[Dict[str, Any]]:
    """读取注册表全部 record（保持源文件顺序）。

    文件缺失/损坏/非预期形态确定性返回 []；仅保留契约字段并归一化（缺 run_id 丢弃）。
    """
    path = resolve_registry_path()
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        return []
    records = payload.get(_KEY, []) if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        return []
    out: List[Dict[str, Any]] = []
    for rec in records:
        norm = normalize_record(rec)
        if norm is not None:
            out.append(norm)
    return out


def get_record(run_id: str) -> Optional[Dict[str, Any]]:
    """按 run_id 查注册表；未命中返回 None。"""
    if not run_id:
        return None
    for rec in read_records():
        if rec["run_id"] == run_id:
            return rec
    return None


def has_records() -> bool:
    """注册表是否持有至少一条有效记录（用于判定聚合 vs 单 task_dir 回落）。"""
    return bool(read_records())


# ============================================================ 写 ====


def _write(records: List[Dict[str, Any]]) -> bool:
    """原子写 records 到注册表文件（临时文件 + rename）；父目录不存在自动创建。

    返回写入是否成功（IO 失败确定性 False，绝不抛异常）。
    """
    path = resolve_registry_path()
    parent = os.path.dirname(path)
    tmp_path = path + ".tmp"
    try:
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump({_KEY: records}, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
        return True
    except OSError:
        try:
            if os.path.isfile(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        return False


def upsert_record(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """登记/更新一条记录（按 run_id 幂等覆盖），并刷新 updated_at。

    返回归一化后的 record；缺 run_id 或写盘失败返回 None。
    """
    norm = normalize_record(record)
    if norm is None:
        return None
    with _LOCK:
        current = read_records()
        ids = [r["run_id"] for r in current]
        if norm["run_id"] in ids:
            # 已存在：原地覆盖（保持文件顺序），幂等。
            current[ids.index(norm["run_id"])] = norm
        else:
            # 新增：追加到末尾。
            current.append(norm)
        if not _write(current):
            return None
        return norm


def set_status(run_id: str, status: str) -> Optional[Dict[str, Any]]:
    """幂等地把某 run 的状态置为 status（用于归档等生命周期更新），并刷新 updated_at。

    返回更新后的 record；run 未命中 / 状态非法 / 写盘失败返回 None。
    """
    if status not in RUN_STATUS:
        return None
    with _LOCK:
        current = read_records()
        found = None
        for i, rec in enumerate(current):
            if rec["run_id"] == run_id:
                found = i
                break
        if found is None:
            return None
        rec = dict(current[found])
        rec["status"] = status
        rec["updated_at"] = now_utc()
        current[found] = rec
        if not _write(current):
            return None
        return rec


def archive_run(run_id: str) -> Dict[str, Any]:
    """幂等地把某 run 标记为 archived（dsh.runs.archive 数据源）。

    返回契约响应 {run_id, status, ok}（extendable）：
    - 成功（含已归档的幂等重复归档）：status='archived'，ok=True；
    - 空 run_id / 注册表未命中 / 写盘失败：status='unknown'，ok=False（确定性，不编造）。
    """
    if not run_id:
        return {"run_id": run_id, "status": "unknown", "ok": False}
    result = set_status(run_id, "archived")
    if result is None:
        return {"run_id": run_id, "status": "unknown", "ok": False}
    return {"run_id": run_id, "status": result["status"], "ok": True}
