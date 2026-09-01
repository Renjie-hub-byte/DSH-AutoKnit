# fw-budget —— 预算闸门模块（需求 5）

> dsh 之上任务编排层 framework-v1 的**预算闸门**：token-meter 记账 → warn/stop 阈值 → 加预算 resume / 放弃归档。
> 输入 = fw-runner（round_004 已审计）的 BudgetGate 钩子 + 快照 schema v3 + `--resume-from-checkpoint` 机制；
> 预算字段语义 = fw-protocol（round_001 已审计）的 `budget` schema 与 effective 默认值；
> 模块任务书的单模块上限语义 = fw-scaffold（round_002 已审计）的派生。

```
┌───────────────────────────────────────────────────────────────┐
│ fw-budget (本模块)                                             │
│  meter:      TokenMeter 记账源（dsh token-meter 适配点 + 本地  │
│              事件流账本等价物）                                 │
│  gate_state: 从 task.yaml budget + 记账源重建 BudgetGate       │
│              （resume 不失忆：历史累计消耗灌回闸门）            │
│  report:     BudgetReport 信息完备（完成/未完成/已试/token/    │
│              排行/warn/stop）                                  │
│  manage:     add-budget（人工加预算）/ archive（放弃归档）/    │
│              run/resume（注入真实 BudgetGate 的首跑与续跑）    │
└───────────────────────────────────────────────────────────────┘
        ▲ 复用(已审计)                            ▲ 对接(后续轮)
  fw-runner BudgetGate/快照/事件流        dsh token-meter（真实接入适配点）
  fw-protocol effective.budget           fw-scaffold 派生模块单模块上限
```

## 快速开始

```bash
# 0) 前置：fw-scaffold 生成任务目录（budget 写进 task.yaml）
PYTHONPATH=<fw1>/fw-scaffold:<fw1>/fw-protocol python3.11 -m fw_scaffold.cli \
    task.yaml --output /tmp/demo

# 1) 首跑（注入真实 BudgetGate；fw-runner 默认 Null 闸门，预算闸门归本模块）
PYTHONPATH=<fw1>/fw-budget:<fw1>/fw-runner:<fw1>/fw-protocol:<fw1>/fw-scaffold \
    python3.11 -m fw_budget.cli run "<任务根>" \
      --executor-cmd "..." --auditor-cmd "..." --json
#    → 预算 70% 输出 warn（含各模块消耗排行）；100% 或单模块超限 → stopped / exit 2

# 2) 看预算状态（信息完备：完成/未完成/已试/token/排行）
python3.11 -m fw_budget.cli status "<任务根>" --json

# 3) 人工加预算 → resume 续跑（已完成不重跑）
python3.11 -m fw_budget.cli add-budget "<任务根>" --max-tokens 200000 --reason "人工复核"
python3.11 -m fw_budget.cli resume "<任务根>" --executor-cmd "..." --auditor-cmd "..." --json
#    或一步：python3.11 -m fw_budget.cli resume "<任务根>" --extra-budget 200000 --json

# 4) 放弃 → 归档（快照标记 archived + 目录移入 archived/）
python3.11 -m fw_budget.cli archive "<任务根>" --reason "预算用尽，放弃交付"
```

其他入口：`./bin/fw-budget ...`、`python3.11 -m fw_budget ...`（等价 cli）。

## 配置（task.yaml budget；fw-protocol effective 默认值）

| 配置项 | 默认 | 含义（fw-budget 消费） |
|---|---|---|
| `budget.max_tokens` | 1000000 | 全局 token 总预算；used ≥ max*stop_at → 硬停 |
| `budget.warn_at` | 0.7 | 用量比例达此值 → warn 预警（含各模块消耗排行；不停机） |
| `budget.stop_at` | 1.0 | 用量比例达此值 → 硬停（goal pause + 快照 + 抛人，exit 2） |
| `budget.per_module_max_tokens` | = max_tokens | 单模块上限；单模块超限 → 硬停（防失控吃光全局） |

协议自检：`warn_at > stop_at` → error；`per_module_max_tokens > max_tokens` → warning（fw-protocol 已实现）。

## 子命令与退出码（机器可解析）

| 子命令 | 作用 | 退出码 |
|---|---|---|
| `status TASK_ROOT [--json]` | 预算状态报告（warn/stop 判定 + 排行 + 完成/未完成/已试/token） | 0 |
| `run TASK_ROOT [--executor-cmd ...] [--json]` | 首次运行：注入真实 BudgetGate | 0=complete / 2=stopped·回人 / 1=输入错 |
| `add-budget TASK_ROOT --max-tokens N [--reason ...] [--json]` | 人工加预算（原子写 task.yaml，保留注释头） | 0 / 1 |
| `resume TASK_ROOT [--extra-budget N] [--executor-cmd ...] [--json]` | 加预算后续跑：重建闸门（累计消耗）→ 快照续跑不重跑 | 0=complete / 2=再停·回人 / 1=已归档等 |
| `archive TASK_ROOT [--reason ...] [--to DIR] [--json]` | 放弃归档：快照标记 archived + 目录 move 到 archived/ | 0 / 1 |

退出码全局语义：`0`=ok / `1`=input_error（任务不可读、已归档…）/ `2`=human（stopped·needs_human 等，信息见 --json 与快照）/ `3`=io_error / `4`=usage。

## 与上下游复用（已审计事实）

- **fw-runner**：本模块不修改 fw-runner 一份代码。`runner.run(budget_gate=...)` 钩子（round_004 已验证：warn/stop/单模块超限/快照 budget_stop）；`--resume-from-checkpoint` 快照机制（completed 不重跑）直接复用。
- **fw-protocol**：预算字段 schema、`warn_at<=stop_at` 校验、`effective` 默认值补全（round_001 已验证）。
- **fw-scaffold**：总预算派生进模块任务书（round_002 已验证）；单模块上限语义在模块级沿用全局。

## dsh token-meter 适配点（真实接入位置如实标注）

需求 5 原文："token 汇总按 dsh token-meter 跨会话统计设计（底部免费能力），框架只做闸门逻辑"。
- **本地等价物**：`EventLogTokenMeter` —— 从 总日志/dispatch.jsonl 事件流归集
  `executor.round.done` / `auditor.round` 的 `detail.tokens`，与 runner 的 `BudgetGate.record`
  同源（每轮 DriverOutcome.tokens 即 dsh token-meter 对接钩子）。未接入 dsh 时框架自洽运行。
- **真实接入点**：`DshTokenMeter._query_dsh()` 为 stub（恒 None → 回退本地账本，source=fallback）。
  接入 dsh 时替换该实现，返回 `{"total": int, "per_module": {...}}`（目标形态：`dsh meter
  --session <run_id>` 跨会话汇总；命令名以 dsh 平台为准），source 变为 dsh，报告/排行口径不变。
  实现示例见 docs/budget-spec.md §适配点。

## 测试

```bash
cd framework-v1/fw-budget
PYTHONDONTWRITEBYTECODE=1 python3.11 -m pytest tests/ -q -p no:cacheprovider
```

覆盖：验收①（warn 事件 + ranking + report phase=warned）、验收②（全局 100% 硬停 + 单模块
超限硬停 + 信息完备 completed/unfinished/tried/token）、验收③（add-budget → resume 零重跑，
函数级 + CLI 级端到端）、meter 统计口径、add-budget 原子写与协议复校验、archive 归档与拒绝、
退出码。

## 已知限制（如实标注）

详见 docs/budget-spec.md §已知限制 —— 摘要：预算检查在**批次边界**（批内模块完成当轮后才 check，
不逐轮 check）；add-budget 改 task.yaml 后 fw-scaffold 再生成需 --force；resume 用事件流账本灌回
闸门（未接入 dsh 时）；warn 重复批可多次 emit；归档后不可自动恢复。
