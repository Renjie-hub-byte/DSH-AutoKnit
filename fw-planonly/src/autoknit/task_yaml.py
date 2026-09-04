"""task.yaml 的构建 / 落盘 / 读取 / 校验。

task.yaml 是 plan-only 的核心产物，承载完整模块拆解 + 接口契约 + 数据契约，
供真人/另一 agent 审阅，也可被后续 run 接续读取。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .data_contract import DATA_CONTRACT, PLAN_ONLY_SUMMARY_INTERFACE
from .errors import InvalidTaskDirError
from .models import ExecutorBlock, Module, TaskPlan

SCHEMA_VERSION = "1.0"
MODULES_KEY = "modules"
FIRST_BLOCK_KEY = "first_block"


def build_task_yaml_dict(plan: TaskPlan) -> dict[str, Any]:
    """把 TaskPlan 组装成可落盘的 task.yaml dict。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "task": {
            "name": plan.task_name,
            "goal": plan.goal,
            "execution_order": plan.execution_order,
        },
        MODULES_KEY: [m.to_dict() for m in plan.modules],
        "interfaces": [PLAN_ONLY_SUMMARY_INTERFACE],
        "data_contract": DATA_CONTRACT,
    }


def save_task_yaml(plan: TaskPlan, path: str | Path) -> Path:
    """把规划落盘为 task.yaml。返回实际写入的路径。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_task_yaml_dict(plan)
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def load_plan_from_task_yaml(path: str | Path) -> TaskPlan:
    """读取并校验 task.yaml，还原为 TaskPlan。

    缺文件 / 缺必要字段 / 类型不对都会抛 :class:`InvalidTaskDirError`（确定性报错），
    供摘要接口在缺 task.yaml 时"确定性空降级并报错"。
    """
    path = Path(path)
    if not path.exists():
        raise InvalidTaskDirError(f"task.yaml 不存在: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:  # pragma: no cover - 由调用方兜底
        raise InvalidTaskDirError(f"task.yaml 解析失败: {exc}") from exc
    if not isinstance(data, dict):
        raise InvalidTaskDirError("task.yaml 顶层必须是 mapping")

    task = data.get("task")
    if not isinstance(task, dict):
        raise InvalidTaskDirError("task.yaml 缺少 task 段")
    name = task.get("name", "untitled-task")
    goal = task.get("goal", "")
    order = task.get("execution_order") or []
    if not isinstance(order, list):
        order = [str(order)]

    raw_modules = data.get(MODULES_KEY)
    if not isinstance(raw_modules, list) or not raw_modules:
        raise InvalidTaskDirError("task.yaml 缺少 modules 列表")

    modules: list[Module] = []
    for item in raw_modules:
        if not isinstance(item, dict) or not item.get("name"):
            raise InvalidTaskDirError("task.yaml 模块缺少 name")
        # BUG-20260903-B 修复：fw-runner scaffold(v1.0.x) 生成的 task.yaml 把行数估算
        # 放在 remaining_estimate.estimate_lines / first_block.estimate_lines（嵌套结构），
        # 模块顶层没有 estimated_lines → _to_int(None) 让 summary 直接炸。
        # 兼容两级 schema：顶层 estimated_lines → remaining_estimate.estimate_lines →
        # first_block.estimate_lines → 0（summary 是报表，缺字段降级为 0，不阻断）。
        re_raw = item.get("remaining_estimate")
        fb0_raw = item.get(FIRST_BLOCK_KEY)
        estimated_raw = item.get("estimated_lines")
        if estimated_raw is None and isinstance(re_raw, dict):
            estimated_raw = re_raw.get("estimate_lines")
        if estimated_raw is None and isinstance(fb0_raw, dict):
            estimated_raw = fb0_raw.get("estimate_lines")
        estimated = _to_int(estimated_raw if estimated_raw is not None else 0,
                            f"模块 {item['name']}.estimated_lines")
        fb_raw = item.get(FIRST_BLOCK_KEY)
        if not isinstance(fb_raw, dict):
            raise InvalidTaskDirError(f"模块 {item['name']} 缺少 first_block")
        fb = ExecutorBlock(
            name=str(fb_raw.get("name", f"{item['name']}/first")),
            scope=str(fb_raw.get("scope", "")),
            estimate_lines=_to_int(fb_raw.get("estimate_lines"), f"模块 {item['name']}.first_block.estimate_lines"),
        )
        modules.append(
            Module(
                name=str(item["name"]),
                scope=str(item.get("scope", "")),
                estimated_lines=estimated,
                first_block=fb,
                dependencies=[str(d) for d in item.get("dependencies", [])],
                interfaces=item.get("interfaces", []) if isinstance(item.get("interfaces"), list) else [],
            )
        )
    return TaskPlan(task_name=str(name), goal=goal, execution_order=[str(o) for o in order], modules=modules)


def _to_int(value: Any, label: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise InvalidTaskDirError(f"task.yaml {label} 必须为整数") from exc
