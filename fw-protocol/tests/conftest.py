"""pytest 公共 fixture：样例加载 + 常用任务书构造器。

运行方式（在 fw-protocol/ 目录下）：
    python3.11 -m pytest tests/ -v
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parent.parent
EXAMPLES = PKG / "examples"


def load_example(name: str) -> dict:
    import yaml
    with open(EXAMPLES / name, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture
def valid_task():
    return load_example("task-valid.yaml")


@pytest.fixture
def cycle_task():
    return load_example("task-cycle.yaml")


@pytest.fixture
def interface_dup_task():
    return load_example("task-interface-dup.yaml")


@pytest.fixture
def conflict_task():
    return load_example("task-conflict.yaml")


def make_module(mid: str, deps=None, interfaces=None, acceptance=None, objective="目标"):
    return {
        "id": mid,
        "name": f"模块{mid}",
        "layer": 1,
        "objective": objective,
        "dependencies": list(deps or []),
        "interfaces": list(interfaces or []),
        "acceptance": list(acceptance or [f"完成{mid}"]),
        "boundaries": [],
    }


def make_task(modules, **overrides):
    doc = {"task": {"name": "测试任务"}, "modules": modules}
    doc.update(deepcopy(overrides))
    return doc
