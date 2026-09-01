"""需求 2 验收 2：每个模块文件夹含全部子目录 + 派生任务书 + 模板文件。"""
from __future__ import annotations

import yaml

from fw_protocol import validate_file

MODULES = ["m01-数据采集", "m02-数据清洗", "m03-报表输出"]


def test_each_module_has_all_subdirs(scaffolded):
    root, _ = scaffolded
    for m in MODULES:
        for sub in ["src", "test", "logs", "tmp"]:
            assert (root / "modules" / m / sub).is_dir(), f"{m} 缺子目录 {sub}"


def test_each_module_has_required_files(scaffolded):
    root, _ = scaffolded
    for m in MODULES:
        mid = m.split("-")[0]
        expected_files = [f"任务书-{mid}.yaml", "REVIEW.md", "contract.yaml", "交付说明.md"]
        for rel in expected_files:
            assert (root / "modules" / m / rel).exists(), f"{m} 缺文件 {rel}"


def test_derived_book_fields_complete_and_consistent(scaffolded):
    """派生模块任务书与总任务书语义一致、字段齐全（逐字段比对）。"""
    root, _ = scaffolded
    master = validate_file(root / "task.yaml").effective
    for m in MODULES:
        mid = m.split("-")[0]
        der = yaml.safe_load((root / "modules" / m / f"任务书-{mid}.yaml").read_text(encoding="utf-8"))
        master_m = next(x for x in master["modules"] if x["id"] == mid)
        for k in ("id", "name", "layer", "objective", "dependencies", "interfaces", "acceptance", "boundaries"):
            assert der["modules"][0][k] == master_m[k], f"{mid} 派生书字段 {k} 不一致"
        assert len(der["modules"]) == 1
        for top in ("task", "budget", "runtime", "integration"):
            assert der[top] == master[top], f"{mid} 派生书顶层 {top} 不一致"


def test_derived_book_header_has_upstream_downstream(scaffolded):
    root, _ = scaffolded
    text = (root / "modules/m02-数据清洗/任务书-m02.yaml").read_text(encoding="utf-8")
    assert "upstream（本模块输入来源）: m01" in text
    assert "downstream（依赖本模块的模块）: m03" in text
    text1 = (root / "modules/m01-数据采集/任务书-m01.yaml").read_text(encoding="utf-8")
    assert "upstream（本模块输入来源）: （无）" in text1
    assert "downstream（依赖本模块的模块）: m02" in text1


def test_review_md_template_machine_parseable(scaffolded):
    root, _ = scaffolded
    text = (root / "modules/m02-数据清洗/REVIEW.md").read_text(encoding="utf-8")
    for key in ("id: m02", "status: pending", "executor_round: 0", "auditor_round: 0",
                "root:", "confidence: 0.0", "## 待办", "## 问题与根因", "## 交接说明"):
        assert key in text, f"REVIEW.md 缺键 {key!r}"


def test_contract_yaml_read_api_prefilled_from_master(scaffolded):
    root, _ = scaffolded
    master = validate_file(root / "task.yaml").effective
    for m in MODULES:
        mid = m.split("-")[0]
        text = (root / "modules" / m / "contract.yaml").read_text(encoding="utf-8")
        master_m = next(x for x in master["modules"] if x["id"] == mid)
        assert f"module: {mid}" in text
        assert f"task: 测试订单管道" in text
        for it in master_m.get("interfaces") or []:
            assert it["path"] in text, f"{mid} contract.yaml 未预填接口 {it['path']}"
            method = it["method"]
            if isinstance(method, list):
                import json as _j
                assert _j.dumps(list(method)) in text, f"{mid} contract.yaml 缺 method 数据字段"
        # 模板占位齐全
        for placeholder in ("input:", "output:", "read_api:"):
            assert placeholder in text


def test_delivery_md_template(scaffolded):
    root, _ = scaffolded
    text = (root / "modules/m01-数据采集/交付说明.md").read_text(encoding="utf-8")
    for key in ("## 改动内容", "## 测试结果", "## 外部验收自测", "## 已知风险"):
        assert key in text
