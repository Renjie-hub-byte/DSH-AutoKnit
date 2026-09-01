"""全角结构体检：AI 产出 YAML 误用全角字符时的显式拦截。

历史背景：全角空格缩进在 PyYAML 里会静默解析成功但结构错位
（`a:\n　　b: 1` → {'a': None, '　　b': 1}），曾导致 first_block
字段静默丢失、模块被全量执行（BUG-001 输入层元凶）。全角冒号做
key 分隔符则直接 ScannerError。本测试锁定 validate_file 的拦截行为。
"""
from pathlib import Path

import pytest

from fw_protocol import validate_file


def _write_tmp(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "task.yaml"
    p.write_text(content, encoding="utf-8")
    return p


def test_fullwidth_indent_is_error(tmp_path):
    p = _write_tmp(tmp_path, "a:\n　　b: 1\nc: 2\n")
    r = validate_file(p)
    codes = [i.code for i in r.errors]
    assert "fullwidth" in codes
    msg = next(i.message for i in r.errors if i.code == "fullwidth")
    assert "第 2 行" in msg and "全角空格" in msg


def test_fullwidth_colon_is_error(tmp_path):
    p = _write_tmp(tmp_path, "first_block：\n  name: x\nb: 2\n")
    r = validate_file(p)
    codes = [i.code for i in r.errors]
    assert "fullwidth" in codes
    msg = next(i.message for i in r.errors if i.code == "fullwidth")
    assert "全角冒号" in msg and "first_block" in msg


_VALID_DOC = """\
task:
  name: 测试任务
  created: '2026-08-25'
  grade: B
  prediction_baseline:
    will_have: []
    will_not_have: []
modules:
- id: m01
  name: 模块一
  layer: 1
  objective: 目标
  dependencies: []
  acceptance:
  - 完成
  boundaries: []
  round_estimate: 1
"""


def test_fullwidth_in_body_not_reported(tmp_path):
    """正文值里的中文全角标点合法，不误报。"""
    p = _write_tmp(tmp_path, "goal: （测试）：说明\nacceptance:\n- 完成：能跑\n")
    # 该文档结构不完整会报 schema，但绝不能报 fullwidth
    codes = [i.code for i in validate_file(p).errors]
    assert "fullwidth" not in codes


def test_ascii_doc_no_fullwidth(tmp_path):
    p = _write_tmp(tmp_path, _VALID_DOC)
    r = validate_file(p)
    assert r.ok, [i.message for i in r.errors]
    assert all(i.code != "fullwidth" for i in r.errors + r.warnings)
