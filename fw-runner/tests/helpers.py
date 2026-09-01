"""共享测试助手：写 task.yaml + 调 fw-scaffold 生成真实 v2 目录树。"""
from __future__ import annotations

from pathlib import Path

import yaml

_FW1 = Path(__file__).resolve().parent.parent.parent
import sys as _sys
for _d in ("fw-runner", "fw-protocol", "fw-scaffold"):
    _p = str((_FW1 / _d).resolve())
    if _p not in _sys.path:
        _sys.path.insert(0, _p)


def write_task_doc(tmp_path: Path, name: str, modules, runtime=None,
                   budget=None, integration_checks=None) -> Path:
    doc = {
        "task": {
            "name": name, "source_prd": "prd/x.md", "owner": "tester",
            "created": "2026-08-21", "grade": "B",
            "prediction_baseline": {"will_have": [f"{name} 产物存在"], "will_not_have": ["不做实时"]},
        },
        "budget": budget or {"max_tokens": 200000},
        "runtime": runtime or {"max_parallel": 2, "executor_max_rounds": 5,
                               "retry_before_switch": 2, "max_executor_switches": 1,
                               "end_gate": "auto"},
        "modules": modules,
        "integration": {
            "contract_file": "contracts/api.yaml",
            "check": integration_checks or {
                "dependency_cycle": True, "interface_duplicate": True,
                "acceptance_conflict": True, "prediction_baseline": True,
                "cross_module_data_dependency": True,
            },
        },
    }
    p = tmp_path / "task.yaml"
    p.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return p


def build_task(tmp_path: Path, name: str, modules, runtime=None, budget=None) -> Path:
    """写 task.yaml → fw-scaffold 生成 v2 目录树 → 返回任务根。"""
    from fw_scaffold.scaffold import generate
    yaml_path = write_task_doc(tmp_path, name, modules, runtime=runtime, budget=budget)
    res = generate(yaml_path, output_dir=tmp_path)
    return res.root


def module(id_: str, name: str, deps=None, layer: int = 1, objective: str = "目标",
           remaining_estimate=None) -> dict:
    m = {
        "id": id_, "name": name, "layer": layer, "objective": objective,
        "dependencies": deps or [],
        "interfaces": [{"path": f"/api/{id_}/*", "method": ["GET"], "note": f"{id_} 接口"}],
        "acceptance": [f"{id_} 验收：按 contract.yaml 产出 src 产物"],
        "boundaries": [f"{id_} 不跨界"],
    }
    if remaining_estimate is not None:
        m["remaining_estimate"] = remaining_estimate
    return m


