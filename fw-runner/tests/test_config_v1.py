"""AutoKnit 配置三通道测试（dflow.yaml + overrides + env）。

RUNTIME_KEYS 白名单 / 三通道合并优先级 / 模型 env 映射。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from fw_runner.config import (
    RUNTIME_KEYS,
    load_yaml_config,
    resolve_combined,
)


@pytest.fixture
def cfgdir(tmp_path: Path) -> Path:
    (tmp_path / "dflow.yaml").write_text(
        """
runtime:
  max_parallel: 2
  split_exit_threshold: 800
  enable_split: true
models:
  executor:
    model: deepseek-v4-flash
    provider: deepseek-official
    reasoning_effort: low
  planner:
    model: deepseek-v4-pro
    reasoning_effort: high
""", encoding="utf-8")
    return tmp_path


def test_load_yaml_config(cfgdir: Path):
    d = load_yaml_config(explicit=str(cfgdir / "dflow.yaml"))
    assert d["runtime"]["split_exit_threshold"] == 800
    assert d["models"]["executor"]["reasoning_effort"] == "low"
    assert d["models"]["planner"]["model"] == "deepseek-v4-pro"


def test_load_yaml_not_found_returns_empty():
    assert load_yaml_config(explicit="/nonexistent/dflow.yaml") == {}


def test_lookup_upward(tmp_path: Path):
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    (tmp_path / "dflow.yaml").write_text("runtime: {max_parallel: 4}\n", encoding="utf-8")
    d = load_yaml_config(cwd=nested)
    assert d["runtime"]["max_parallel"] == 4


def test_resolve_combined_overrides_highest(cfgdir: Path):
    res = resolve_combined(
        cfg={"max_parallel": 3},              # 任务书 runtime
        overrides={"max_parallel": 5},        # CLI 最高
        explicit_config=str(cfgdir / "dflow.yaml"),
    )
    ro = res["runtime_overrides"]
    assert ro["max_parallel"] == 5            # CLI 覆盖 yaml 和 cfg
    assert ro["split_exit_threshold"] == 800  # yaml 生效
    assert ro["enable_split"] is True


def test_resolve_combined_whitelist_ignores_unknown_key(cfgdir: Path):
    bad = cfgdir / "bad.yaml"
    bad.write_text("runtime: {max_parallel: 2, bogus_threshold: 999}\n", encoding="utf-8")
    res = resolve_combined(explicit_config=str(bad))
    assert "bogus_threshold" not in res["runtime_overrides"]
    assert "max_parallel" in res["runtime_overrides"]


def test_resolve_combined_model_env(cfgdir: Path):
    res = resolve_combined(explicit_config=str(cfgdir / "dflow.yaml"))
    menv = res["model_env"]
    assert menv["FW_EXECUTOR_MODEL"] == "deepseek-v4-flash"
    assert menv["FW_EXECUTOR_REASONING"] == "low"
    assert menv["FW_PLANNER_MODEL"] == "deepseek-v4-pro"
    assert menv["FW_PLANNER_REASONING"] == "high"
    # 没配的 split 不出现
    assert "FW_SPLIT_MODEL" not in menv


def test_runtime_keys_includes_split_and_parallel():
    assert "split_exit_threshold" in RUNTIME_KEYS
    assert "enable_split" in RUNTIME_KEYS
    assert "max_parallel" in RUNTIME_KEYS