# fw-planonly —— AutoKnit plan-only 模式（只规划给人审）

> AutoKnit v2 需求1：先 plan 不执行，产物 task.yaml 给人 / 另一个 agent 审，同意后再 run。

## 是什么

`autoknit plan-only <dir>` 只跑 planner：读 PRD → 拆模块 → 产出 `task.yaml`（含
接口契约 + 数据契约）→ 写 checkpoint → 打印摘要，规划完即停（不调 executor/auditor/split，
绝不给现有框架 LLM 加请求，token 账本恒为 0）。

## 用法

```bash
autoknit plan-only 任务-xxx          # 产出 task.yaml + 摘要 + checkpoint
autoknit summary 任务-xxx            # 只看摘要（共几模块/每模块行数/首个 executor 行数）
# 审完同意 → 接续执行（用同一 task.yaml，不重复规划）：
autoknit run 任务-xxx --resume-from-checkpoint
```

## 摘要输出

```
plan-only 规划摘要：共 N 个大模块，预计总行数 X
  m01 xxx | 预计 800 行 | 首个 executor 任务 200 行
```

## 实现

`src/autoknit/`：prd_parser / planner / task_yaml / checkpoint / resume / summary / ledger。
独立 python 包，依赖 pyyaml + jsonschema。

## 测试

```bash
cd fw-planonly && PYTHONPATH=src python3.11 -m pytest test/ -q   # 34 passed
```
