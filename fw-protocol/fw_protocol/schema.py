"""schema 加载与默认值套用。

- DEFAULT_SCHEMA_PATH : 包内置 schema 位置（也可用 --schema 覆盖）
- load_schema(path)   : 读取并解析 JSON Schema
- DEFAULT_VALUES      : 未显式填写时套用的默认值（与 STATUS.md / 执行配置-v0.4 一致）
- apply_defaults(doc) : 对任务书深拷贝套用默认值，返回新 dict（不改原对象）
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "task-schema.json"

# 默认值：任务级配置（执行配置-v0.4 定稿）+ 协议 v0.1 的模块级默认
DEFAULT_VALUES: Dict[str, Any] = {
    "task": {"prediction_baseline": {"will_have": [], "will_not_have": []}},
    "budget": {
        "max_tokens": 1000000,
        "warn_at": 0.7,
        "stop_at": 1.0,
        "per_module_max_tokens": None,  # None → 套用后置为 max_tokens（即不单独限制）
    },
    "runtime": {
        "models": {
            "planner": "deepseek-v4-pro",
            "executor": "deepseek-v4-flash",
            "auditor": "deepseek-v4-flash",
        },
        "max_parallel": 3,
        "executor_max_rounds": 5,
        "retry_before_switch": 2,
        "max_executor_switches": 1,
        "end_gate": "auto",
    },
    "integration": {
        "contract_file": "contracts/api.yaml",
        "check": {
            "dependency_cycle": True,
            "interface_duplicate": True,
            "method_semantic": True,
            "acceptance_conflict": True,
            "prediction_baseline": True,
            "cross_module_data_dependency": True,
        },
    },
    "module": {
        "dependencies": [],
        "interfaces": [],
        "boundaries": [],
        # 注意：round_estimate 不注入 None 默认——scaffold 会把 effective 序列化落盘为 task.yaml，
        # None 会撞 schema（integer），且复校验失败。未预估 = 键缺席（persona 强制填写，校验器兜底）。
        "max_rounds_override": None,   # 未填 → apply_defaults 继承 runtime.executor_max_rounds（整数，可安全落盘）
    },
}


def load_schema(path: Optional[Path] = None) -> Dict[str, Any]:
    """读取 JSON Schema。path 缺省用包内置 schema。"""
    p = Path(path) if path is not None else DEFAULT_SCHEMA_PATH
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def _merge_defaults(target: Dict[str, Any], defaults: Dict[str, Any]) -> None:
    """原地向 target 补默认值（只补缺失 key；已有 key 不覆盖）。"""
    for key, val in defaults.items():
        if key not in target:
            target[key] = copy.deepcopy(val)
        elif isinstance(val, dict) and isinstance(target[key], dict):
            _merge_defaults(target[key], val)


def apply_defaults(doc: Dict[str, Any]) -> Dict[str, Any]:
    """返回套用默认值后的任务书深拷贝（原 doc 不被修改）。"""
    out = copy.deepcopy(doc)
    if not isinstance(out, dict):
        return out
    _merge_defaults(out, {"task": DEFAULT_VALUES["task"], "budget": DEFAULT_VALUES["budget"],
                          "runtime": DEFAULT_VALUES["runtime"], "integration": DEFAULT_VALUES["integration"]})
    # per_module_max_tokens 未填 → 等于 max_tokens（不单独限制）
    budget = out.get("budget")
    if isinstance(budget, dict) and budget.get("per_module_max_tokens") is None:
        budget["per_module_max_tokens"] = budget.get("max_tokens", DEFAULT_VALUES["budget"]["max_tokens"])
    modules = out.get("modules")
    if isinstance(modules, list):
        # runtime.executor_max_rounds 已由顶层默认值补全（缺省 5）→ 模块级 max_rounds_override 默认继承
        rt = out.get("runtime")
        inherited = rt.get("executor_max_rounds") if isinstance(rt, dict) else None
        if not isinstance(inherited, int):
            inherited = DEFAULT_VALUES["runtime"]["executor_max_rounds"]
        for m in modules:
            if isinstance(m, dict):
                _merge_defaults(m, DEFAULT_VALUES["module"])
                if m.get("max_rounds_override") is None:
                    m["max_rounds_override"] = inherited
    return out
