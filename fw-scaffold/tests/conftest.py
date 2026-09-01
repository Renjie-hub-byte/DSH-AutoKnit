"""fw-scaffold 测试夹具：把 framework-v1 根与 fw-protocol 加入 sys.path，提供合法任务书 fixture。

测试全部写入 pytest 的 tmp_path（零写入模式：-p no:cacheprovider + PYTHONDONTWRITEBYTECODE=1）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

FW1 = Path(__file__).resolve().parent.parent.parent          # framework-v1
SCAFFOLD_DIR = Path(__file__).resolve().parent.parent        # fw-scaffold
for p in (str(SCAFFOLD_DIR), str(FW1 / "fw-protocol")):
    if p not in sys.path:
        sys.path.insert(0, p)


# 合法任务书：3 模块，依赖链 m01→m02→m03（与 fw-protocol examples/task-valid.yaml 同构，
# 自包含不依赖外部文件）；刻意省略 per_module_max_tokens，验证 effective 默认值补全。
VALID_TASK_YAML = """\
task:
  name: 测试订单管道
  owner: 审计员
  created: 2026-08-21
  grade: B
  prediction_baseline:
    will_have:
      - 订单数据落盘为 JSON
      - 清洗模块产出标准化订单
    will_not_have:
      - 不做实时流式处理

budget:
  max_tokens: 100000
  warn_at: 0.7
  stop_at: 1.0

runtime:
  models:
    planner: deepseek-v4-pro
    executor: deepseek-v4-flash
    auditor: deepseek-v4-flash
  max_parallel: 3
  executor_max_rounds: 5
  retry_before_switch: 2
  max_executor_switches: 1
  end_gate: auto

integration:
  contract_file: contracts/api.yaml

modules:
  - id: m01
    name: 数据采集
    layer: 1
    objective: 读取原始订单 CSV 并落盘为 JSON
    dependencies: []
    interfaces:
      - path: /api/order/*
        method: [POST, PUT]
        note: 订单写入接口
    acceptance:
      - 输入样例 CSV 后生成 src/data/orders.json
      - 空文件输入时输出空数组而非报错
    boundaries:
      - 不做数据清洗

  - id: m02
    name: 数据清洗
    layer: 1
    objective: 对采集到的订单标准化清洗与字段校验
    dependencies: [m01]
    interfaces:
      - path: /api/order/*
        method: [GET]
        note: 清洗后订单查询接口
    acceptance:
      - 对 orders.json 清洗后产出 cleaned_orders.json
      - 非法记录标记 drop_reason 且总数可核对
    boundaries:
      - 不产出聚合报表

  - id: m03
    name: 报表输出
    layer: 1
    objective: 按日聚合清洗后的订单输出统计 CSV
    dependencies: [m02]
    interfaces:
      - path: /api/report/*
        method: [POST]
        note: 报表生成接口
    acceptance:
      - 对 cleaned_orders.json 聚合出 daily_orders.csv
      - 有测试覆盖空数据与单日多单
    boundaries:
      - 只读 m02 产物
"""


@pytest.fixture
def valid_task(tmp_path):
    """写一份合法 task.yaml 到 tmp_path，返回路径。"""
    p = tmp_path / "task.yaml"
    p.write_text(VALID_TASK_YAML, encoding="utf-8")
    return p


@pytest.fixture
def scaffolded(valid_task, tmp_path):
    """在 tmp_path/out 下生成目录树，返回 (root, result)。"""
    from fw_scaffold import generate
    out = tmp_path / "out"
    result = generate(valid_task, output_dir=out)
    return result.root, result
