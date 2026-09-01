"""需求 2 验收 1：输入合法 task.yaml → 目录树生成、无缺失。"""
from __future__ import annotations

import yaml

from fw_scaffold import generate
from fw_scaffold.derive import task_dir_name


def test_root_dir_named_by_task_and_date(scaffolded):
    root, _ = scaffolded
    assert root.exists() and root.is_dir()
    assert root.name == "任务-测试订单管道_2026-08-21"          # created 2026-08-21 生效


def test_task_dir_name_helper():
    import datetime as dt
    from fw_scaffold.derive import task_dir_name, sanitize_name
    eff = {"task": {"name": "A/B 任务", "created": "2026-08-21"}}
    assert task_dir_name(eff) == "任务-A-B-任务_2026-08-21"      # 非法字符转 -；日期取 created
    eff2 = {"task": {"name": "无日期任务"}}
    assert task_dir_name(eff2, today=dt.date(2026, 8, 21)) == "任务-无日期任务_2026-08-21"  # 缺 created 用今天
    assert sanitize_name('a/b\\c:d*e?f"g<h>i|j') == 'a-b-c-d-e-f-g-h-i-j'


def test_top_level_files_exist(scaffolded):
    root, _ = scaffolded
    expected = ["task.yaml", "contracts/api.yaml", "skeleton.md"]
    for rel in expected:
        assert (root / rel).exists(), f"缺失顶层文件: {rel}"


def test_top_level_dirs_exist(scaffolded):
    root, _ = scaffolded
    for rel in ["认知", "shared", "总日志", "modules"]:
        assert (root / rel).is_dir(), f"缺失顶层目录: {rel}"


def test_logs_dir_initialized(scaffolded):
    root, _ = scaffolded
    for rel in ["总日志/dispatch.jsonl", "总日志/integration.jsonl", "总日志/快照.json"]:
        assert (root / rel).exists(), f"缺失总日志文件: {rel}"
    import json
    dispatch = json.loads((root / "总日志/dispatch.jsonl").read_text(encoding="utf-8"))
    assert dispatch["event"] == "scaffold"
    snapshot = json.loads((root / "总日志/快照.json").read_text(encoding="utf-8"))
    assert snapshot["status"] == "scaffolded"
    assert set(snapshot["modules"]) == {"m01", "m02", "m03"}


def test_module_dirs_for_all_modules(scaffolded):
    root, _ = scaffolded
    for mid_name in ["m01-数据采集", "m02-数据清洗", "m03-报表输出"]:
        assert (root / "modules" / mid_name).is_dir(), f"缺失模块目录: {mid_name}"


def test_task_yaml_is_effective_with_defaults(scaffolded):
    """根 task.yaml = effective 版本：省略的 per_module_max_tokens 被补全。"""
    root, _ = scaffolded
    doc = yaml.safe_load((root / "task.yaml").read_text(encoding="utf-8"))
    assert doc["budget"]["per_module_max_tokens"] == 100000      # 默认补全 = max_tokens
    assert doc["budget"]["warn_at"] == 0.7
    assert doc["integration"]["check"]["prediction_baseline"] is True
    # 与 fw-protocol effective 一致
    from fw_protocol import validate_file
    eff = validate_file(root / "task.yaml").effective
    assert eff == doc


def test_no_extra_top_level_unexpected(scaffolded):
    """除清单文件外，顶层只应有规范中的条目。"""
    root, _ = scaffolded
    allowed = {"task.yaml", "contracts", "skeleton.md", "认知", "shared", "总日志", "modules",
               ".scaffold-manifest.json", ".scaffold-version"}
    top = {e.name for e in root.iterdir()}
    assert top - allowed == set(), f"出现规范外顶层条目: {top - allowed}"
