# fw-budget 预算闸门设计说明（需求 5 / v0.4）

> 本文档是 fw-budget 的规格与实现对照：配置、验收对照、事件/快照/账本数据流、dsh
> token-meter 适配点、已知限制。供 auditor 零写入复现与后续轮（需求 6/7）消费。

## 1. 数据流

```
fw-runner 每轮 DriverOutcome.tokens ──► BudgetGate.record(mid, tokens)
        │                                    │
        ▼                                    ▼
dispatch.jsonl 事件流 ◄──── budget.warn / budget.stop（含 budget 快照 + ranking）
        │                                    │
        ▼                                    ▼
EventLogTokenMeter（本地账本）      BudgetGate.check()（warn_at / stop_at / per_module）
        │                                    │
        └─────────► BudgetReport（信息完备：completed/unfinished/tried/tokens/ranking/phase）
                              │
                    status CLI │ resume 前重建闸门（累计消耗灌回）
```

- **记账**：每轮 executor/auditor 的 tokens（dsh token-meter 对接钩子）被 runner 写进事件流；
  本模块只**汇总**（EventLogTokenMeter），**不自己记账**（"框架只做闸门逻辑"，需求 5 原文）。
- **判定**：全局 `used >= max_tokens*stop_at` → 硬停；`used >= max_tokens*warn_at` → 预警（不停机）；
  单模块 `per_module[id] >= per_module_max_tokens` → 硬停。全部复用 fw-runner BudgetGate（round_004 已审计）。

## 2. 与三条验收的对照

| 验收 | 复现路径 | 证据形态 |
|---|---|---|
| ① 70% 预警 + 各模块消耗排行 | `fw-budget run`（tokens 注入）→ 事件流 `budget.warn`（detail.budget.warned=True + detail.ranking 降序）；`fw-budget status` phase=warned + meter.ranking | 测试 test_acceptance1_*（3 用例）+ 事件流证据 |
| ② 100% 或单模块超限 → 硬停 + 抛人（信息完备） | `fw-budget run` → status=stopped / exit 2 / 快照 cause=budget_stop；report：completed / unfinished / tried（executor_round）/ meter.total + ranking / stop_message | 测试 test_acceptance2_*（2 用例，全局+单模块） |
| ③ 加预算 resume 从快照继续不重跑 | `add-budget` → `resume`：事件流 exec_done 仅 [m01,m02,m03] 各 1 次；seq 从 budget.stop 后延续；executor_round 不重置 | 测试 test_acceptance3_*（3 用例）+ test_cli_e2e_resume（CLI 级） |

信息完备字段定义：
- `completed` = 快照 completed_order（完成模块）
- `unfinished` = 快照 modules 中非 done 且不在 completed 的模块（含 pending/running/needs_human）
- `tried` = 每个模块已试 executor 轮数（快照 per_module.executor_round；未试 = 0）
- `tokens` = meter.total（全局）+ meter.per_module（每模块）+ ranking（降序）

## 3. resume 不失忆机制（本模块对 runner 钩子的适配补充）

fw-runner 的 `--resume-from-checkpoint` 恢复 `state.budget_used_tokens` 但**不**把累计消耗
灌回内存态 BudgetGate（round_004 已审计行为）。fw-budget 的 `resume` 在调用
`runner.run(resume=True, budget_gate=...)` 前：
1. `EventLogTokenMeter`（或 dsh）归集历史每模块消耗；
2. `build_budget_gate` 构造真实 BudgetGate 并 `record` 历史消耗；
3. 传给 runner → resume 后每轮新消耗叠加，check 基于**累计**（跨 resume 不失忆）。

不改 fw-runner 任何代码；本行为已在 README 与本文档如实标注。

## 4. 适配点：dsh token-meter 跨会话统计

需求原文："token 汇总：dsh token-meter 跨会话统计（底部免费能力），框架只做闸门逻辑"。

- **目标接入形态**（真实接入时替换 `DshTokenMeter._query_dsh`）：
  ```python
  class DshTokenMeter(TokenMeter):
      def _query_dsh(self) -> Optional[dict]:
          # dsh token-meter 跨会话汇总（按 run_id 查 executor/auditor 全部会话）
          # 例：subprocess.run(["dsh", "meter", "--session", self.run_id, "--json"])
          # 返回 {"total": int, "per_module": {mid: int}}；失败/未接入返回 None
          return None   # ← 当前 stub：回退本地账本
  ```
- **兜底**：`_query_dsh` 返回 None → `source=fallback`（EventLogTokenMeter 本地账本），
  框架仍可跑（与 fw-runner NullBudgetGate 的"未就绪不卡主循环"一致）。
- 报告/排行口径不随接入方式变化（都是 total + per_module + ranking）。

## 5. add-budget 语义（人工动作）

- 人工加预算 = 更新 task.yaml 的 `budget.max_tokens`（fs 原子写：临时文件 + fsync + os.replace，
  保留 fw-scaffold 说明注释头）；改后任务书仍通过 fw-protocol 校验（resume 时 runner 重新加载有效版本）。
- **单模块上限同步**：effective 版本里 `per_module_max_tokens` 默认 = 旧全局值；若它仍等于旧全局值，
  视为"默认跟随全局"，加预算时同步提升；用户显式配置的独立单模块上限保持不动。
- 三权分立：加预算/归档是**人工动作**，fw-budget 只提供落地命令与原子防护，不代替真人决策
  （executor 永不自定验收标准，需求 3 铁律）。

## 6. archive 语义（放弃）

- 快照 status → archived（cause=budget_abandoned，原子写）→ 整个任务根 move 到
  `<父>/archived/<任务目录名>-<时间戳>/` → 新位置写 ARCHIVE.md（原路径/时间/原因/预算摘要）。
- 归档后 `resume`/`status` 拒绝（防误续跑）；`archive` 拒绝重复归档（含原路径已移走的友好报错）。

## 7. 事件流消费口径

- `executor.round.done` / `auditor.round` 的 `detail.tokens` 计入（与 BudgetGate.record 同源）。
- `budget.warn` / `budget.stop` 事件的 `detail` 含 `{budget: BudgetStatus.to_dict(), ranking: [...]}`，
  报告提取为"预算事件证据"。
- fw-scaffold 初始化的 `scaffold` 事件无 seq 字段（写入总日志）；runner 事件 seq 从 1 开始、
  resume 从 last_seq+1 延续（round_004 已审计，本模块只读消费）。

## 8. CLI 示例与退出码

```bash
# 快速开始全链（真实 CLI 级，test_cli_e2e_resume.py 同款）
PYTHONPATH=<fw1>/fw-budget:<fw1>/fw-runner:<fw1>/fw-protocol:<fw1>/fw-scaffold \
  python3.11 -m fw_budget.cli run "<任务根>" --executor-cmd "..." --auditor-cmd "..." --json
# exit 0=complete / 2=stopped·回人 / 1=input_error / 3=io / 4=usage
```

## 9. 已知限制（如实标注）

1. **预算检查在批次边界**：runner 在每批模块完成后 check，不逐轮 check。若单批内某模块一轮
   耗完预算，该模块会完成当轮后停（快照记 stopped，未完成模块不再启动）。这是 runner 既有
   语义（round_004 已审计），fw-budget 不改变，[README 快速开始]与验收报告据此解读。
2. **resume 记账源**：未接入 dsh 时 resume 用事件流账本灌回闸门（source=fallback）。若历史
   事件被轮转（重新从零 run）会丢失旧账本——本框架单 run_id 滚动场景不涉及（文档标注）。
3. **add-budget 后 scaffold 防护**：改 task.yaml 会使其指纹变化；再跑 fw-scaffold 会因
   expected 版本防护拒绝（需 --force）。这是期望行为（task.yaml 属任务输入，不被 scaffold 覆盖）。
4. **warn 可多次 emit**：预算跨批持续 ≥warn_at 时，每批后都可能 emit budget.warn（report 只读
   展示全部，CLI 摘要取最新判定）；不停机。
5. **归档不可自动恢复**：archive 是放弃动作；恢复需人工从 archived/ 移回并手动改快照状态，
   fw-budget 不提供恢复命令（防误操作）。
6. **per_module_max_tokens 显式配置小于新全局预算**：add-budget 只同步"默认跟随全局"的单模块上限；
   显式更小值保持，此时单模块超限仍可能先于全局触发硬停（协议给 warning 语义）。
7. **单模块超限硬停而非升级链**：需求 5 要点提"进升级链"，但验收②明确"单模块超限 → 硬停 + 抛人"；
   本实现与 fw-runner 既有 BudgetGate（round_004 已验证的"单模块超限 → stop"）一致，采用**硬停 + 抛人**，
   信息完备（未完成模块/已试轮数/token 在报告与快照中齐备，人工可据此加预算或放弃）。

## 10. 文件清单

```
fw-budget/
├── README.md                     # 架构/快速开始/配置/退出码/适配点/测试/已知限制
├── docs/budget-spec.md           # 本文档（规格 + 验收对照 + 数据流 + 适配点 + 限制）
├── pyproject.toml
├── bin/fw-budget                 # 可执行入口（shebang python3.11）
├── fw_budget/
│   ├── __init__.py  __main__.py  cli.py
│   ├── meter.py                  # TokenMeter / EventLogTokenMeter / DshTokenMeter（适配点）
│   ├── gate_state.py             # build_budget_gate / check_now / load_effective_budget
│   ├── report.py                 # BudgetReport / build_report / human_summary
│   └── manage.py                 # add_budget / archive / run_first / resume / resume_advice
└── tests/                        # 28+ 用例（验收①/②/③ + meter/manage/cli/e2e）
```
