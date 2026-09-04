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




def unavailable_split_driver():
    """显式表达"拆分不可用"前提的 split 驱动（2026-09-04 小澈复查 N1）。

    背景：test_acceptance3 / test_heartbeat / test_root_cause_routing /
    test_progress_snapshot / test_subprocess_drivers 这 5 条要验的路由是
    「升级链用尽 → 尝试 SPLIT → 拆分失败 → 回人」，但它们把"拆分失败"这个前提
    **寄托在环境巧合上**（docstring 自陈"缺 fw-split.sh"）。BUG-20260903-A① 把
    fw-split.sh 修好之后前提自动失效：不设 FW_SPLIT_MODE 时真起 dsh（烧钱 + flaky），
    设成 demo 时拆分真成功 → 断言集体崩。

    现在前提由测试自己注入，不再依赖环境，也不会碰真模型。
    exit 2 是刻意的：让 _classify_call_failure 归到 infra，走 SplitInfraError
    → 不回喂 LLM → split_failed → 回人，与用例想守的语义一致。
    """
    from fw_runner.drivers import InlineAgentDriver
    from fw_runner.model import DriverOutcome

    def fn(ctx):
        return DriverOutcome(
            status="error", reason="注入的拆分故障（模拟缺 fw-spawn.py / dsh 未就绪）",
            detail={"exit": 2, "stderr": "injected: split unavailable"})
    return InlineAgentDriver(fn)
