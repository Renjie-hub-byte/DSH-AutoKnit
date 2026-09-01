# fw-runner —— 执行编排主循环（需求 4）

> dsh 之上任务编排层 framework-v1 的**执行编排**模块：依赖图拓扑分层 → 并行调度（≤ max_parallel）
> → 每模块 executor → auditor → 升级链 → 心跳守护 → checkpoint/resume。
> 输入 = fw-protocol 校验通过的任务书 + fw-scaffold 生成的 v2 目录树（本轮已审计的前置）。

```

┌─────────────────────────────────────────────────────────────┐
│ fw-runner (本模块)                                           │
│  plan_batches: 依赖图拓扑就绪集贪心分批 ≤ max_parallel       │
│  run_batch:    批内并行（sessions.fork 本地等价物）          │
│  run_module:   executor 轮 → auditor 判定 → 升级链 → 心跳    │
│  checkpoint:   总日志/快照.json（fs 原子写） + resume         │
│  钩子:         budget_hook（需求5）/ integrate_hook（需求6） │
└─────────────────────────────────────────────────────────────┘
        ▲ 复用(已审计)                    ▲ 对接(后续轮)
  fw-protocol.validate_file().effective  fw-budget / fw-integrate
  fw-scaffold 模块目录/REVIEW.md/总日志   dsh: sessions.fork / token-meter
```

## 快速开始

```bash
# 1) 用 fw-scaffold 生成任务目录（顶层 task.yaml + 模块结构 + 总日志三件套）
#    示例任务书见 examples/task-3modules.yaml（3 模块依赖链，fw-protocol 校验通过）
PYTHONPATH=<fw1>/fw-scaffold:<fw1>/fw-protocol python3.11 -m fw_scaffold.cli \
    examples/task-3modules.yaml --output /tmp/demo

# 2) 执行编排（默认 demo 子进程驱动：executor 落产物、auditor 核验收）
./bin/fw-runner run "/tmp/demo/任务-示例-订单管道_2026-08-21" --json

# 3) 中断后续跑（不重跑已完成模块）
./bin/fw-runner run "<任务根>" --resume-from-checkpoint --json
```

其他入口：`python3.11 -m fw_runner.cli run ...`、`python3.11 -m fw_runner run ...`（仅脚本级）。

## 配置（RunConfig：effective.runtime 默认值 + CLI 覆盖 + 模式开关）

| 配置项 | 默认 | CLI 覆盖 | 含义 |
|---|---|---|---|
| `runtime.max_parallel` | 3 | `--max-parallel N` | 同层独立模块并行上限 |
| `runtime.executor_max_rounds` | 5 | `--executor-max-rounds N` | 单模块 executor 轮数上限（超了判卡循环换人/回人） |
| `runtime.retry_before_switch` | 2 | `--retry-before-switch N` | auditor 打回 N 次后换 executor |
| `runtime.max_executor_switches` | 1 | `--max-executor-switches N` | 最多换几个 executor，再卡回人 |
| `runtime.end_gate` | auto | `--end-gate auto\|always` | auto=异常才回人 / always=每任务人工确认 |
| runner 级 `heartbeat_n_rounds` | 2 | `--heartbeat-n N` | 连续 N 轮无实质产出判静默卡死 |
| runner 级 `checkpoint_every` | 1 | `--checkpoint-every N` | 每 N 模块完成写快照 |
| 模式开关 `mode` | speed_first | `--mode speed_first\|cost_first` | 见下 |

**模式开关（speed_first / cost_first）**：
- `speed_first`（默认）：max_parallel 用 runtime 原值，追求吞吐。
- `cost_first`：省 token/会话 → 并行上限压到 `min(max_parallel, 2)`，同 executor 打回耐心
  `retry_before_switch+1`（少换人少开销）。完整策略差异归需求 7 文档。
- 显式 CLI 覆盖优先于模式。

## 退出码（机器可解析）

| 码 | 含义 |
|---|---|
| 0 | run_complete：全部模块完成 + 集成钩子通过/延迟，无回人 |
| 1 | input_error：任务根/任务书不可运行 |
| 2 | needs_human：升级链上限回人 / upstream\|contract 根因 / end_gate=always / 集成失败 / 预算硬停 |
| 3 | io_error：运行期 IO/意外异常 |
| 4 | usage：CLI 用法错误 |
| 130 | interrupted：运行被中断（已写快照，`--resume-from-checkpoint` 续跑） |

`--json` 输出 `{ok,status,exit_reason,run_id,task_root,checkpoint,completed,needs_human,
failed,tokens_used,duration_s,seq_events,config,modules,integration,payload}`。

## 需求 4 验收对照（测试逐条复现，见 tests/）

1. **4 独立模块 + max_parallel=3 → 3 并行 + 1 排队**
   `test_acceptance1_parallel.py`：观测最大并发 == 3（线程安全计数）；m04 开始 ≥ 批次1 全部结束；
   批次结构 `[[m01,m02,m03],[m04]]`。
2. **依赖链 A→D → D 等 A 完成才启动**
   `test_acceptance2_dependency.py`：批次 `[[A],[D]]`；D 开工时 shared/done-A 标记必须存在；
   D 开始时刻 ≥ A 结束时刻。
3. **block 2 次 → 换 executor（读 REVIEW.md 交接）→ 上限回人**
   `test_acceptance3_upgrade_chain.py`：executor 身份序列 `[E1,E1,E2,E2]`；新 executor 首轮读 REVIEW
   见 `root=self/status=blocked`（交接）；logs/handover-* 三件套 bundle 生成；最终 needs_human，
   REVIEW 机器键 `block_total=4/root=self/status=needs_human`。
4. **中断后 --resume-from-checkpoint 从快照接续不重跑**
   `test_acceptance4_resume.py`：m03 第 1 轮 interrupted → 快照 status=interrupted（m01/m02 done）；
   resume 后 m01/m02 executor **不再被调用**（调用记录为多重集断言），m03 续跑第 2 轮完成；
   run_id 延续、事件 seq 从快照 last_seq 续号。

另有：心跳守护（静默卡死→升级链）、失败根因分流（upstream/contract 直接抛人不重试）、
checkpoint 字段/原子性/resume 计数续接、事件 seq 单调不重复、预算/集成钩子、CLI 退出码、子进程 spawn。

## 三权分立（写进 driver 契约）

- **executor 永不自定验收标准**：只写内容小节（已做/待办/产物/交付说明），判定权在 auditor。
- **auditor 只判不写执行**：auditor 判定（verdict/root/confidence/reason/blocker）以 outcome 带回，
  由 runner 统一把机器可解析状态键写回 REVIEW.md（**单一写者** + fs 原子写，无需外部锁）。
- **runner 只调度不代定验收**：升级链/换人/回人决策按 RETRY/SWITCH/HUMAN 规则执行，不替 auditor 定标准。

## 与 dsh 免费能力映射（不造轮子）

| dsh 能力 | runner 落地 | 说明 |
|---|---|---|
| sessions.fork（并行省缓存） | `fork.py: ForkRunner/run_parallel` + `drivers.ScriptedAgentDriver`(子进程) | 本地形态；dsh 部署替换为 sessions.fork(scope=run_id, module=id) |
| fs 原子写（并发防护） | `io_utils.atomic_write_text` | 同目录临时 + fsync + os.replace |
| 事件 seq（完整性） | `events.EventLog`（线程安全单调 seq） | dispatch.jsonl |
| sessionProjections checkpoint | `checkpoint.py` 快照.json | fs 原子写 + resume |
| session-query（审计） | `review.py` REVIEW.md 机器键 + `upgrade.sync_review` | auditor 判定四段可机器解析 |
| token-meter（记账） | `budget_hook.BudgetGate`（钩子；token 源默认 0） | dsh token-meter 绑定归 fw-budget 轮 |

## 已知限制（诚实标注，详见 docs/runner-spec.md §限制）

1. **桩 executor/auditor 代理**：本轮的并行度/依赖链/升级链/checkpoint 验收用确定性 inline 驱动 +
   子进程 demo 驱动复现；真实 dsh preset（fw-planner/executor/auditor）联动属需求 3 轮 + 端到端轮。
2. **sessions.fork 本地等价物**：`run_parallel` 为 ThreadPoolExecutor；dsh 部署需把 ForkRunner
   换成 sessions.fork 适配器（接口已留，`drivers.ScriptedAgentDriver` 即子进程会话形态）。
3. **token 来源未接**：budget_used_tokens 来自 driver outcome.tokens（默认 0）；dsh token-meter
   跨会话统计由 fw-budget 轮接入，届时 NullBudgetGate 换真 gate。
4. **集成验收仅钩子**：契约运行时校验/预测基线对照归 fw-integrate（需求 6 轮）；runner 只负责
   调用钩子并按 failed/end_gate 决策。
5. **中断原子性**：中断批次内同批其他模块的未完成工作不会回滚；resume 后未完成模块按快照续跑
   （已完成的永不重跑）。
6. **心跳 = 轮次级守卫生**：检测"连续 N 轮无实质产出"，不检测单轮内挂死（进程级超时属于 dsh
   session 基础设施）；agent 进程非零退出/超时按 agent_error 进升级链。
7. **REVIEW 机器键以 runner 为准**：driver 写内容小节，runner 写状态键；若 driver 自行改状态键
   会被覆盖（单一写者纪律，防并发写冲突）。
8. **同一任务根重复从零 run（非 resume）**：旧 run_id 的 dispatch.jsonl 会被自动归档为
   `总日志/dispatch-archive-<时间戳>.jsonl`（保留审计轨迹），新 run 从干净 seq=1 开始；
   事件完整性按 **run 域** 保证（单次 run 或 resume 链内严格单调）。resume 不旋转，延续同一链。

## 测试

```bash
cd fw-runner && PYTHONDONTWRITEBYTECODE=1 python3.11 -m pytest tests/ -p no:cacheprovider -q
# 期望：44 passed
```
