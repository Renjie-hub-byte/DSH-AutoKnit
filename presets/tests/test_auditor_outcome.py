"""需求3 验收3：auditor 输出四段（判定/blocker/root/confidence）机器可解析。

复现路径（auditor 可独立执行）：
1. examples/*.json 全部通过 validate_outcome（四段齐全 + 枚举/范围合法）；
2. examples/*-report-*.md 的审计报告可提取出 AUDIT_RESULT 四段行，且与对应 JSON 四段一致；
3. 与 fw-runner DriverOutcome 消费字段对齐（guarded 导入，缺 fw-runner 时跳过）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fw_presets import (  # noqa: E402
    build_four_segment_line,
    extract_four_segment_line,
    load_outcome,
    parse_four_segment_line,
    validate_outcome,
)

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
JSON_FILES = sorted(EXAMPLES.glob("auditor-outcome-*.json"))
REPORT_FILES = sorted(EXAMPLES.glob("auditor-report-*.md"))

assert JSON_FILES and REPORT_FILES, "examples 缺失"


@pytest.mark.parametrize("path", JSON_FILES, ids=lambda p: p.name)
def test_example_json_four_segments_machine_parseable(path: Path) -> None:
    """每个示例 JSON：四段齐全 + 校验通过 + load_outcome 可回读。"""
    d = json.loads(path.read_text(encoding="utf-8"))
    for seg in ("verdict", "blocker", "root", "confidence"):
        assert seg in d, f"{path.name} 缺四段之一 {seg}"
    ok, errors = validate_outcome(d)
    assert ok, f"{path.name} 校验失败: {errors}"
    # load_outcome 要求能按 schema 重读（canonical 机器契约成立）
    assert load_outcome(path)["verdict"] == d["verdict"]


@pytest.mark.parametrize("rpath", REPORT_FILES, ids=lambda p: p.name)
def test_report_md_four_segment_line_extractable(rpath: Path) -> None:
    """每个报告 .md 末尾四段行可提取，且与同名 JSON 一致。"""
    jpath = EXAMPLES / (rpath.name.replace("auditor-report-", "auditor-outcome-").replace(".md", ".json"))
    assert jpath.is_file(), f"{rpath.name} 缺对应 JSON {jpath.name}"
    text = rpath.read_text(encoding="utf-8")
    line = extract_four_segment_line(text)
    assert line is not None, f"{rpath.name} 未提取到 AUDIT_RESULT 行"
    d = json.loads(jpath.read_text(encoding="utf-8"))
    assert line["verdict"] == d["verdict"], f"{rpath.name} verdict 不一致"
    assert line["root"] == d["root"], f"{rpath.name} root 不一致"
    assert line["confidence"] == str(d["confidence"]), f"{rpath.name} confidence 不一致"
    assert line["blocker"] == d["blocker"], f"{rpath.name} blocker 不一致"


def test_four_segment_line_roundtrip() -> None:
    """build → parse 往返一致；parse 结果四个段齐全。"""
    d = {"verdict": "block", "blocker": "验收项2 缺失", "root": "self", "confidence": 0.5}
    line = build_four_segment_line(d)
    assert line.startswith("AUDIT_RESULT|")
    parsed = parse_four_segment_line(line)
    assert parsed == {"verdict": "block", "blocker": "验收项2 缺失", "root": "self", "confidence": "0.5"}


def test_negative_invalid_outcomes_rejected() -> None:
    """非法判定被拒：坏 root / 超范围 confidence / 缺 blocker / blocker 含 |。"""
    ok, errs = validate_outcome({"verdict": "block", "blocker": "x", "root": "banana", "confidence": 0.5})
    assert not ok and any("root" in e for e in errs)
    ok, errs = validate_outcome({"verdict": "pass", "blocker": "", "root": "", "confidence": 1.5})
    assert not ok and any("confidence" in e for e in errs)
    ok, errs = validate_outcome({"verdict": "block", "root": "self", "confidence": 0.5})
    assert not ok and any("blocker" in e for e in errs)
    ok, errs = validate_outcome({"verdict": "block", "blocker": "a|b", "root": "self", "confidence": 0.5})
    assert not ok and any("blocker" in e for e in errs)
    ok, errs = validate_outcome({"verdict": "block", "blocker": "x", "root": "", "confidence": 0.5})
    assert not ok and any("root" in e for e in errs)


def test_schema_file_present_and_valid() -> None:
    """protocol/auditor-outcome.schema.json 存在且为合法 JSON（四段 required）。"""
    schema_path = Path(__file__).resolve().parent.parent / "protocol" / "auditor-outcome.schema.json"
    assert schema_path.is_file()
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert "verdict" in schema["required"]
    assert "blocker" in schema["required"]
    assert "root" in schema["required"]
    assert "confidence" in schema["required"]


def test_alignment_with_fw_runner_driver_outcome() -> None:
    """与 fw-runner DriverOutcome 消费字段对齐（guarded：缺 fw-runner 则跳过）。"""
    runner_pkg = Path(__file__).resolve().parent.parent.parent / "fw-runner"
    if not (runner_pkg / "fw_runner" / "model.py").is_file():
        pytest.skip("未找到 fw-runner，跳过对齐测试")
    sys.path.insert(0, str(runner_pkg))
    try:
        from fw_runner.model import DriverOutcome  # noqa: F401
        fields = {f for f in DriverOutcome.__dataclass_fields__}  # type: ignore[attr-defined]
    except Exception:
        pytest.skip("fw-runner 导入失败，跳过对齐测试")
    # auditor 判定相关字段必须在 preset schema 中表达（verdict/root/confidence/blocker/reason）
    for f in ("verdict", "root", "confidence", "blocker", "reason", "tokens"):
        assert f in fields, f"fw-runner DriverOutcome 缺少字段 {f}"
    schema = json.loads((Path(__file__).resolve().parent.parent / "protocol"
                         / "auditor-outcome.schema.json").read_text(encoding="utf-8"))
    for f in ("verdict", "root", "confidence", "blocker", "reason", "tokens", "detail"):
        assert f in schema["properties"], f"preset schema 缺少 fw-runner 对齐字段 {f}"
