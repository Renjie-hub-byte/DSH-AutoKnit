"""run 注册表（~/.autoknit/runs.json）—— fw-runner 侧登记/更新程序段。

数据契约（data_contract stores[runs_registry]，与 fw-data-bridge fwapi/registry.py 同款，
全链路唯一事实源，禁止自定义路径/字段/枚举/ts 格式）：
- 默认路径：``~/.autoknit/runs.json``，可用环境变量 ``AUTOKNIT_RUNS_REGISTRY`` 覆盖绝对路径。
- 每 record 字段：run_id / task_dir / task / status / started_at / updated_at。
- status 枚举：active / complete / archived；写入只按契约枚举（启动 active、收官 complete）。
- ts 存储格式：ISO-8601 UTC（``YYYY-MM-DDTHH:MM:SS+00:00``）。
- 文件格式：``{"runs": [ ... ]}``；缺失/损坏确定性读为 ``[]``；写入幂等、原子（临时文件+rename）。

职责边界：
- 本模块是注册表的**写入方**（fw-runner 启动/收官自动登记，程序段，非 LLM 角色），
  与 fw-data-bridge 的 fwapi.registry（读取/归档方）按同一契约读写同一文件；
- dashboard 数据桥（/api/runs）只读注册表聚合展示，此处登记后新 run 自动可见；
- 失败仅告警返回 False，**绝不阻塞/抛异常**（dashboard 挂了不能挡跑任务）。
- 纯标准库实现，无第三方运行时依赖；跨进程/线程写用临时文件+rename 原子替换。

注意：任务书/旧现网条目曾写 ``running``，但契约枚举是 active|complete|archived，
面板只对 ``status === 'active'`` 的 run 提供归档按钮并自动跟随；``running`` 会被
数据桥归一化为 ``unknown``（不跟随、不可归档）。故启动登记用 ``active``（v1.0.1 修正）。
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# 契约枚举（对齐 data_contract shared_enums.run_status；fwapi/registry.py RUN_STATUS）。
RUN_STATUS: tuple = ("active", "complete", "archived")

# 注册表文件默认路径（~/.autoknit/runs.json，可被 AUTOKNIT_RUNS_REGISTRY 覆盖为绝对路径）。
REGISTRY_REL = os.path.join("~", ".autoknit", "runs.json")

# 环境变量覆盖键（对齐 data_contract stores[runs_registry].env_var）。
ENV_REGISTRY = "AUTOKNIT_RUNS_REGISTRY"

# 注册表文件顶层键。
_KEY = "runs"

# 记录合法字段集合（落盘/读盘时据此清洗，防止未知字段污染）。
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


# ============================================================ 写 ====


def register_run(run_id: str, task_dir: str, task: str,
                 status: str = "active",
                 registry_path: Optional[str] = None) -> bool:
    """幂等登记一条 run 记录（fw-run 启动时调用）。

    - 首次（新 run / 冷启动裸跑）：新建记录，status 默认 active（契约枚举，启动即被
      dashboard 数据桥可见并自动跟随），started_at/updated_at 记当前 UTC；
    - 已存在（resume 续跑沿用同一 run_id）：**跳过不修改**（幂等，不重复插入、不覆盖
      既有 status/started_at —— 任务要求「已存在则跳过」；resume 的实时状态由数据桥
      从快照 stage 派生，注册表 status 仅管排序/归档）；
    - registry_path 显式给出时优先于环境变量/默认路径（测试隔离用）。
    任何失败（缺 run_id / 目录不可写 / JSON 损坏 / 非法 status）仅返回 False 并静默告警，
    绝不抛异常 —— dashboard 挂了不能挡跑任务。
    """
    if not run_id or not task_dir:
        return False
    if status not in RUN_STATUS:
        return False
    path = os.path.abspath(os.path.expanduser(registry_path)) if registry_path \
        else resolve_registry_path()
    with _LOCK:
        current = _read_records_path(path)
        if any(rec["run_id"] == run_id for rec in current):
            return True   # 幂等：已存在则跳过（resume 续跑沿用同一 run_id）
        record = {
            "run_id": run_id,
            "task_dir": task_dir,
            "task": task,
            "status": status,
            "started_at": now_utc(),
            "updated_at": now_utc(),
        }
        current.append(record)
        return _write_path(path, current)


def complete_run(run_id: str, registry_path: Optional[str] = None) -> bool:
    """收官时把某 run 标记为 complete（幂等），并刷新 updated_at；未命中返回 False。

    失败绝不抛异常（仅返回 False）。resume 后真正收官也会调用 —— 该 run 一直以
    active 挂在注册表，直到全部完成/回人/中断等终态才置 complete。
    """
    if not run_id:
        return False
    path = os.path.abspath(os.path.expanduser(registry_path)) if registry_path \
        else resolve_registry_path()
    with _LOCK:
        current = _read_records_path(path)
        found = None
        for i, rec in enumerate(current):
            if rec["run_id"] == run_id:
                found = i
                break
        if found is None:
            return False
        rec = dict(current[found])
        rec["status"] = "complete"
        rec["updated_at"] = now_utc()
        current[found] = rec
        return _write_path(path, current)


# ============================================================ 内部工具 ====


def _read_records_path(path: str) -> List[Dict[str, Any]]:
    """按显式路径读注册表记录（register_run/complete_run 用，隔离环境变量影响）。"""
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


def _write_path(path: str, records: List[Dict[str, Any]]) -> bool:
    """按显式路径原子写注册表；IO 失败确定性 False，绝不抛异常。"""
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
