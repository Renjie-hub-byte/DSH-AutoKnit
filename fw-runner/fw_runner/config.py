"""AutoKnit 配置（三通道：dflow.yaml + CLI overrides + env）。

2026-08-28 开源预备：把散落的阈值/并行/模型/思考模式参数统一收进一个配置入口。
优先级（低 → 高）：dflow.yaml 默认值 < env 变量 < CLI overrides。

只负责"读配置、合并、返回 dict"，不持有逻辑；实际生效在 context._resolve_runtime_config
（阈值/并行）与 bin/{executor,auditor,split}.sh（模型/思考模式，env 透传）。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

# 任务书 runtime 里能覆盖的阈值/并行键（与 context._resolve_runtime_config 白名单对齐）
RUNTIME_KEYS = (
    "max_parallel", "executor_max_rounds", "retry_before_switch",
    "max_executor_switches", "heartbeat_n_rounds", "checkpoint_every", "end_gate",
    "mode", "split_max_depth", "split_max_total", "split_exit_threshold",
    "retry_remaining_threshold",
    "split_protocol_retries", "audit_require_evidence", "enable_split",
)

# 各角色的物理键映射：yaml 键 → (env 键, CLI/model-patch 键)
ROLE_MODEL_KEYS = {
    "planner": "FW_PLANNER_MODEL",
    "executor": "FW_EXECUTOR_MODEL",
    "auditor": "FW_AUDITOR_MODEL",
    "split": "FW_SPLIT_MODEL",
}
ROLE_REASONING_KEYS = {
    "planner": "FW_PLANNER_REASONING",
    "executor": "FW_EXECUTOR_REASONING",
    "auditor": "FW_AUDITOR_REASONING",
    "split": "FW_SPLIT_REASONING",
}
ROLE_PROVIDER_KEYS = {
    "planner": "FW_PLANNER_PROVIDER",
    "executor": "FW_EXECUTOR_PROVIDER",
    "auditor": "FW_AUDITOR_PROVIDER",
    "split": "FW_SPLIT_PROVIDER",
}
ROLES = ("planner", "executor", "auditor", "split")


def _lookup(path: Path, cwd: Path | None = None) -> Path:
    """在 cwd 及向上父目录中查找 dflow.yaml / autoKnit.yaml（项目级配置）。"""
    start = (cwd or Path.cwd()).resolve()
    for d in (start, *start.parents):
        for name in ("dflow.yaml", "autoKnit.yaml", ".dflow.yaml"):
            cand = d / name
            if cand.is_file():
                return cand
    return path  # 显式路径兜底（可能不存在，调用方处理）


def load_yaml_config(explicit: Optional[str] = None,
                     cwd: Optional[Path] = None) -> dict:
    """读 dflow.yaml（项目级）→ dict。

    键约定：
      runtime:  {max_parallel, executor_max_rounds, ..., split_exit_threshold, enable_split}
      models:   {planner/executor/auditor/split: {model, provider, reasoning_effort}}
    显式 explicit 路径优先；否则向上查找 autoKnit.yaml/dflow.yaml；找不到返回 {}。
    """
    if explicit:
        p = Path(explicit)
    else:
        p = _lookup(Path("non-existent"), cwd or Path.cwd())  # 只在 cwd 链查
    if not p or not p.is_file():
        return {}
    try:
        import yaml
    except ImportError:
        return {}
    try:
        return dict(yaml.safe_load(p.read_text(encoding="utf-8")) or {})
    except Exception:
        return {}


def resolve_combined(cfg: Mapping[str, Any] | None = None,
                     overrides: Mapping[str, Any] | None = None,
                     explicit_config: Optional[str] = None,
                     cwd: Optional[Path] = None) -> Dict[str, Any]:
    """三通道合并 → 返回统一 dict。

    返回两段：
      runtime_overrides : 阈值/并行等，喂给 context._resolve_runtime_config
      model_env          : 模型/思考模式 env 键值，调用方 setenv
    优先级：dflow.yaml < overrides(CLI)。
    """
    yc = load_yaml_config(explicit_config, cwd)
    runtime = dict((yc.get("runtime") or {}))
    runtime.update(dict(cfg or {}))            # cfg（任务书 runtime）仅次于 yaml 之后
    runtime.update({k: v for k, v in dict(overrides or {}).items() if v is not None})
    # 只保留白名单键，防 yaml 里写错键静默喂进 RunConfig 之外
    runtime_out = {k: runtime[k] for k in RUNTIME_KEYS if k in runtime}

    model_env: Dict[str, str] = {}
    mmodels = yc.get("models") or {}
    for role in ROLES:
        rc = mmodels.get(role) or {}
        if isinstance(rc, dict):
            if rc.get("model"):
                model_env[ROLE_MODEL_KEYS[role]] = str(rc["model"])
            if rc.get("reasoning_effort"):
                model_env[ROLE_REASONING_KEYS[role]] = str(rc["reasoning_effort"])
            if rc.get("provider"):
                model_env[ROLE_PROVIDER_KEYS[role]] = str(rc["provider"])
    return {"runtime_overrides": runtime_out, "model_env": model_env}


def env_to_model_env() -> Dict[str, str]:
    """从当前 os.environ 读已设置的 FW_*_MODEL/REASONING/PROVIDER，合并返回（不覆盖已有）。"""
    out: Dict[str, str] = {}
    for key in (*ROLE_MODEL_KEYS.values(), *ROLE_REASONING_KEYS.values(),
                *ROLE_PROVIDER_KEYS.values()):
        v = os.environ.get(key)
        if v:
            out[key] = v
    return out