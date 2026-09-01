"""需求3 产物完整性：presets/ 目录三 preset 文件齐全 + 元数据可解析 + 文档存在。"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fw_presets import persona as persona_mod  # noqa: E402

PRESETS = Path(__file__).resolve().parent.parent
PRESET_NAMES = ("fw-planner", "fw-executor", "fw-auditor")


@pytest.mark.parametrize("name", PRESET_NAMES)
def test_preset_dir_files_complete(name: str) -> None:
    """每个 preset 目录含 preset.yml + agent.cordis.yml。"""
    d = PRESETS / name
    assert (d / "preset.yml").is_file(), f"{name}/preset.yml 缺失"
    assert (d / "agent.cordis.yml").is_file(), f"{name}/agent.cordis.yml 缺失"


@pytest.mark.parametrize("name", PRESET_NAMES)
def test_preset_yml_parses_with_metadata(name: str) -> None:
    """preset.yml：name / description / order 齐全。"""
    doc = yaml.safe_load((PRESETS / name / "preset.yml").read_text(encoding="utf-8"))
    assert isinstance(doc, dict)
    assert str(doc.get("name", "")).strip(), f"{name} preset.yml 缺 name"
    assert str(doc.get("description", "")).strip(), f"{name} preset.yml 缺 description"
    assert isinstance(doc.get("order"), int), f"{name} preset.yml 缺 order"


@pytest.mark.parametrize("name", PRESET_NAMES)
def test_agent_cordis_yml_parses_with_persona(name: str) -> None:
    """agent.cordis.yml：YAML 列表 + persona 块。"""
    data = persona_mod.load_agent_cordis(PRESETS / name)
    assert isinstance(data, list) and data, f"{name} agent.cordis.yml 解析失败"
    ids = [item.get("id") for item in data if isinstance(item, dict)]
    assert "persona" in ids, f"{name} agent.cordis.yml 缺 persona 块"


def test_readme_and_spec_exist() -> None:
    """README.md 与 docs/presets-spec.md 存在（挂载方式/三权分立语义）。"""
    assert (PRESETS / "README.md").is_file(), "presets/README.md 缺失"
    assert (PRESETS / "docs" / "presets-spec.md").is_file(), "docs/presets-spec.md 缺失"


def test_protocol_and_examples_exist() -> None:
    """protocol schema + 四段行规范 + 示例齐全。"""
    assert (PRESETS / "protocol" / "auditor-outcome.schema.json").is_file()
    assert (PRESETS / "protocol" / "four-segment-line.md").is_file()
    examples = sorted((PRESETS / "examples").glob("auditor-outcome-*.json"))
    reports = sorted((PRESETS / "examples").glob("auditor-report-*.md"))
    assert len(examples) >= 4, f"示例 JSON 不足: {len(examples)}"
    assert len(reports) >= 4, f"示例报告 MD 不足: {len(reports)}"
    assert len(examples) == len(reports)
