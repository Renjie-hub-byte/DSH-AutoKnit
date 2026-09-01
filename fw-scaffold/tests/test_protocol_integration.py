"""与 fw-protocol 的复用契约：只接受 fw-protocol 判定合法的任务书；conflict 输入生成但提示。"""
from __future__ import annotations

import yaml

import pytest

from fw_scaffold import ExpectedVersionMismatch, TaskInvalidError, generate

CONFLICT_TASK = """\
task:
  name: 冲突任务
  created: 2026-08-21
modules:
  - id: m01
    name: 极速交付模块
    layer: 1
    objective: 用最快速度上线支付功能
    acceptance:
      - 抢在 3 天内全量上线
      - 不做安全评审直接发布
    boundaries:
      - 不引入任何额外审核流程
"""


def test_rejects_interface_duplicate_book(valid_task, tmp_path):
    import yaml as _y
    doc = _y.safe_load(valid_task.read_text(encoding="utf-8"))
    doc["modules"][1]["interfaces"] = doc["modules"][0]["interfaces"]   # m02 与 m01 同前缀+方法
    dup = tmp_path / "dup.yaml"
    dup.write_text(_y.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    with pytest.raises(TaskInvalidError) as ei:
        generate(dup, output_dir=tmp_path / "out")
    r = ei.value.args[0]
    assert any(i.code == "interface_duplicate" for i in r.errors)
    assert not (tmp_path / "out").exists()


def test_rejects_schema_invalid_book(valid_task, tmp_path):
    doc = yaml.safe_load(valid_task.read_text(encoding="utf-8"))
    doc["modules"] = []                        # 空模块清单违反 schema minItems>=1
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    with pytest.raises(TaskInvalidError):
        generate(bad, output_dir=tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_conflict_input_still_generates_with_warning(tmp_path):
    """验收冲突（快 vs 安全）由 fw-protocol 标记为 conflict（非 error）；scaffold 生成结构并提示。"""
    p = tmp_path / "conflict.yaml"
    p.write_text(CONFLICT_TASK, encoding="utf-8")
    r = generate(p, output_dir=tmp_path / "out")
    assert r.root.exists()
    assert r.conflicts, "应携带冲突提示"
    assert any("speed" in c and "safety" in c for c in r.conflicts)   # fw-protocol 消息用组名 speed/safety


def test_effective_passed_through_to_derived_books(valid_task, tmp_path):
    """派生书预算/运行配置来自 effective（补默认），不丢字段。"""
    r = generate(valid_task, output_dir=tmp_path / "out")
    der = yaml.safe_load((r.root / "modules/m01-数据采集/任务书-m01.yaml").read_text(encoding="utf-8"))
    assert der["budget"]["per_module_max_tokens"] == 100000
    assert der["runtime"]["max_parallel"] == 3
    assert der["integration"]["contract_file"] == "contracts/api.yaml"
