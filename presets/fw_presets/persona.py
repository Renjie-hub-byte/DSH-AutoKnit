"""preset persona 只读加载辅助（验收2 铁律检查用）。

- iter_presets()            -> [(dir_name, preset_dir)]  三个 preset 目录
- load_agent_cordis(dir)    -> list                     解析 agent.cordis.yml（YAML 列表）
- get_persona_text(dir)     -> str                      提取 persona 块 config.text

说明：dsh 的 agent.cordis.yml 含 `!!js <expr>`（js-yaml 求值）标签，PyYAML safe_load 不认识；
本模块用 TolerantLoader 把未知标签按普通标量保留（仅用于只读校验，不执行任何 JS 表达式），
既保持文件与 dsh 原生格式一致，又保证 python 侧可解析复现。
本模块只读；测试与 auditor 复现均以磁盘真实文件为准。
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator, List, Optional, Tuple

import yaml

PRESETS_DIR = Path(__file__).resolve().parent.parent
PRESET_NAMES = ("fw-planner", "fw-executor", "fw-auditor")


class _TolerantLoader(yaml.SafeLoader):
    """SafeLoader 子类：未知/!!js 标签按原文本标量保留（不做 JS 求值）。"""

    def construct_yaml_unknown(self, node):  # type: ignore[no-untyped-def]
        if isinstance(node, yaml.ScalarNode):
            return node.value
        if isinstance(node, yaml.SequenceNode):
            return [self.construct_object(child, deep=True) for child in node.value]
        if isinstance(node, yaml.MappingNode):
            out = {}
            for k, v in node.value:
                out[self.construct_object(k, deep=True)] = self.construct_object(v, deep=True)
            return out
        return None


_TolerantLoader.add_constructor(None, _TolerantLoader.construct_yaml_unknown)


def iter_presets() -> Iterator[Tuple[str, Path]]:
    """yield (预设名, preset 目录)。"""
    for name in PRESET_NAMES:
        d = PRESETS_DIR / name
        if d.is_dir():
            yield name, d


def load_agent_cordis(preset_dir: str | Path) -> Optional[List[dict]]:
    """解析 agent.cordis.yml（YAML 列表）；不可解析返回 None。"""
    p = Path(preset_dir) / "agent.cordis.yml"
    if not p.is_file():
        return None
    try:
        data = yaml.load(p.read_text(encoding="utf-8"), Loader=_TolerantLoader)
        return data if isinstance(data, list) else None
    except Exception:
        return None


def get_persona_text(preset_dir: str | Path) -> str:
    """提取 persona 块 config.text；找不到返回空串。"""
    data = load_agent_cordis(preset_dir) or []
    for item in data:
        if isinstance(item, dict) and item.get("id") == "persona":
            cfg = item.get("config") or {}
            text = cfg.get("text")
            if isinstance(text, str):
                return text
    return ""
