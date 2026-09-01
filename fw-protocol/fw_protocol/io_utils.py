"""YAML 读取统一封装（safe_load + 错误包装），供 cli 与 validate_file 复用。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, TextIO

import yaml


class TaskYamlError(Exception):
    """task.yaml 无法解析（文件缺失 / YAML 语法错）。"""


def yaml_load_safe(stream: TextIO) -> Any:
    try:
        return yaml.safe_load(stream)
    except yaml.YAMLError as e:
        raise TaskYamlError(f"YAML 解析失败: {e}") from e


def read_task_document(path: str | Path) -> Any:
    p = Path(path)
    if not p.exists():
        raise TaskYamlError(f"文件不存在: {p}")
    with open(p, "r", encoding="utf-8") as f:
        return yaml_load_safe(f)
