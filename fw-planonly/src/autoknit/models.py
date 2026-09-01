"""领域模型：planner 产出的模块拆解与整份规划。

这些 dataclass 是 task.yaml 与摘要/checkpoint 的唯一事实来源，
序列化逻辑集中在 task_yaml.py / checkpoint.py，模型本身不负责 IO。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ExecutorBlock:
    """一个 executor 任务块（模块下的第一个执行任务）。"""

    name: str
    scope: str
    estimate_lines: int


@dataclass
class Module:
    """一个大模块（顶层拆解单元）。"""

    name: str
    scope: str
    estimated_lines: int
    first_block: ExecutorBlock
    dependencies: list[str] = field(default_factory=list)
    interfaces: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaskPlan:
    """整份规划：元信息 + 模块列表。"""

    task_name: str
    goal: str
    execution_order: list[str]
    modules: list[Module]

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_name": self.task_name,
            "goal": self.goal,
            "execution_order": self.execution_order,
            "modules": [m.to_dict() for m in self.modules],
        }
