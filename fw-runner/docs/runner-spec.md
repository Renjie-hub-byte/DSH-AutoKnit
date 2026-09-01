# fw-runner 规格（runner-spec v1）

> 本文档给出 fw-runner 的实现规格：调度算法、升级链状态机、心跳守卫生、checkpoint 协议、
> 事件 schema、驱动契约、验收复现矩阵、已知限制。与需求 4 逐条对应。

## 1. 调度（scheduler.py）

- `build_edges(modules)`：`{id: [deps]}`，只保留模块集合内依赖（合法任务书无未知依赖）。
- `plan_batches(modules, max_parallel, completed=())`：就绪集贪心分批。
  - 每批取当前就绪（依赖全部完成）的前 `max_parallel` 个；
  - 批内并行、批间串行 —— 这是"同层独立模块并行 ≤ max_parallel"与"下游等上游完成"的落地形态；
  - `completed`（resume 场景）视为已满足，其依赖不再阻塞。
- 环 → `CycleError`（防御性；fw-protocol 已在上游拒绝环任务书，级联保护）。
- `topological_layers`：Kahn 分层（layer 0=无依赖），供诊断/报告。

> 注意分层与分批的差别：分层给出理论并行层；分批用"就绪集贪心"保证批次大小 ≤ max_parallel，
> 并在下游启动前强制上游**完成**（同一层内也不能让下游先跑）。

## 2. 升级链（upgrade.py）—— 状态机

```
            ┌──────────────────────────── round loop ────────────────────────────┐
            │                                                                     │
   executor 轮 → 指纹判定 substance ──卡死 N 轮──▶ heartbeat.stall（等效 block/self）
            │                                                                     │
            ▼                                                                     │
   auditor 判定                                                                （continue）
   verdict = pass ──────────▶ module.done                                        │
   verdict = block，root ∈ {upstream, contract} ──▶ HUMAN（直接抛人，不重试）      │
   verdict = block，root = self/stall ──▶ route_verdict:                          │
       block_count < retry_before_switch          → RETRY（同 executor 下一轮）    │
       block_count ≥ retry_before_switch，switches < max_executor_switches → SWITCH│
       （block_count 清零；交接三件套写 logs/handover-*；executor_id=E{switches+1}）│
       其余                                        → HUMAN（上限回人）             │
            └────────────────────────────────────────────────────────────────────┘
```

- **失败根因分流**：`upstream`/`contract` 根因拒绝重试（重试无意义，直接抛人并带 REVIEW 信息）。
- **交接三件套** = REVIEW.md（含判定与交接说明）+ contract.yaml + 交付说明.md；写模块 `logs/`（豁免区）。
- **卡死**（executor_max_rounds 超限 / agent 崩溃 / 心跳卡死）不给 retry（防同一 executor 死循环），
  直接 switch 或 human。
- 计数字段（block_count/block_total/executor_switches/stall_count）在 `runner` 侧记账，
  `finalize_human` 时全量回写 REVIEW 机器键（单一写者）。

## 3. 心跳守卫生（heartbeat.py + runner）

- substance 判定 = REVIEW.md 已做节 + status + src/ test/ 文件（名/大小/mtime）+ 交付说明.md
  的指纹（`review.fingerprint`）变化；`logs/`、`tmp/` 为豁免区不计。
- 每轮无实质产出 → `stall_count += 1`；`stall_count ≥ heartbeat_n_rounds` → 判静默卡死 →
  按 block/self 进升级链（先于 auditor，避免给同一 executor 无限机会）。

## 4. checkpoint 协议（总日志/快照.json，schema_version=3）

```jsonc
{
  "schema_version": 3, "run_id": "...", "task": "任务名", "updated_at": "...",
  "status": "running|needs_human|stopped|interrupted|complete|integration_failed|needs_confirmation",
  "cause": "checkpoint_every|escalated_to_human|budget_stop|interrupted|all_modules_done|...",
  "modules": {"m01": "pending|running|done|needs_human", ...},
  "dependencies": {"m01": [], ...},
  "failure_counts": {"m01": 0, ...},
  "per_module": {"m01": {"executor_id":"E1","executor_round":1,"auditor_round":1,
                         "block_total":0,"executor_switches":0,"stall_count":0,...}},
  "needs_human": [], "completed_order": [],
  "budget_used_tokens": 0, "last_seq": 42
}
```

- 写入时机：每 `checkpoint_every` 模块完成 + 关键状态转移（回人/预算停/中断/结束）都原子写。
- 写入方式：`io_utils.atomic_write_text`（同目录临时 + fsync + os.replace）——并发安全不需要锁。
- resume：`--resume-from-checkpoint` → 读快照恢复 RunState → 已完成模块不重跑（executor/auditor
  不再被调用），计数器从快照续接，run_id 延续，事件 seq 从 `last_seq` 续号。
- fw-scaffold 初始化为 schema_version=2（仅初始化占位）；runner 首次写升级为 3（含 per_module）。

## 5. 事件 schema（总日志/dispatch.jsonl，一行一事件）

```jsonc
{"seq": 1, "ts": "ISO8601", "run_id": "run-...", "event": "run.start",
 "module": null, "action": null, "detail": {...}}
```

事件类型：`run.start` / `run.resume` / `module.dispatch` / `executor.round.start` /
`executor.round.done` / `executor.round.error` / `heartbeat.stall` / `auditor.round.start` /
`auditor.round` / `module.blocked` / `executor.switch` / `module.needs_human` / `module.done` /
`budget.warn` / `budget.stop` / `integration.check`。

seq 在 `EventLog` 锁内自增+追加，**严格单调不重复**（批次内并行模块并发 emit 也安全；
scaffold 初始化写入的 1 行无 seq 字段，为兼容遗留）。

- **重复 run 旋转**：同一任务根**从零**重新 run（非 resume）时，若 dispatch.jsonl 已含其他
  run_id 的事件流，runner 先把旧文件归档为 `总日志/dispatch-archive-<时间戳>.jsonl`（保留审计
  轨迹），再让新 run 从干净 seq=1 开始 —— 事件完整性按 **run 域** 保证。resume 路径不旋转，
  延续同一 run_id 链（seq 从快照 last_seq 续号）；显式注入 event_log 的调用方自行负责。

## 6. 驱动契约（drivers.py）

- `AgentDriver.run_round(ctx) -> DriverOutcome`；ctx 含 `module / run_id / role / round_no /
  executor_id / task_root / mode / env`（env 注入 MODULE_DIR/TASK_ROOT/RUN_ID/ROUND/ROLE/
  EXECUTOR_ID/MODE + PYTHONPATH）。
- `DriverOutcome`：`status(ok|error|interrupted) / verdict(pass|block) / root(self|upstream|contract
  |stall) / confidence 0-1 / reason / blocker / substance / tokens / detail`。
- 子进程驱动：退出码 0=ok（读 `tmp/{role}-outcome.json`，无则按 REVIEW 兜底）；13=interrupted；
  其他非零=agent_error（进升级链）。子进程 cwd=模块目录。
- 三权分立：executor 写内容小节不判验收；auditor 判验收不写执行；runner 写状态键不代定标准。

## 7. 验收复现矩阵（需求 4）

| 验收 | 复现方式 | 断言要点 | 测试 |
|---|---|---|---|
| 1. 4 独立 maxp=3 → 3 并行 + 1 排队 | inline 驱动 + 并发计数 | max_active==3；m04.start≥批次1.end；批次 [[3],[1]] | test_acceptance1_parallel.py |
| 2. 依赖链 A→D | inline 驱动 + shared 标记 | 批次 [[A],[D]]；D 开工见 shared/done-A；时序 D.start≥A.end | test_acceptance2_dependency.py |
| 3. block 2 次→换 executor→上限回人 | inline 驱动（auditor 恒 block/self） | 身份序列 [E1,E1,E2,E2]；E2 首轮读 REVIEW 见 root=self；handover bundle；needs_human | test_acceptance3_upgrade_chain.py |
| 4. 中断后 resume 不重跑 | inline 驱动（m03 第 1 轮 interrupted） | 快照 interrupted；resume 后 m01/m02 零重跑；m03 续跑第 2 轮；run_id 延续；seq 续号 | test_acceptance4_resume.py |
| 心跳守护 | inline 驱动（substance=False 恒假） | heartbeat_n=1 → 心跳优先升级；root=stall；exec=4/audit=0 | test_heartbeat.py |
| 根因分流 | 参数化 auditor root | upstream/contract → exec=1 即回人、switches=0、无 bundle | test_root_cause_routing.py |
| checkpoint 协议 | 快照读写往返 | 字段齐全（modules/dependencies/failure_counts/per_module/last_seq）；原子 JSON；resume 计数续接 | test_checkpoint.py |
| 事件 seq | 日志解析 | 单调不重复；resume 续号 last_seq+1；run_id 恒定 | test_event_seq.py |
| 预算/集成钩子 | BudgetGate / 自定义 hook | warn 事件含排行；stop 硬停+快照 cause；failed→integration_failed；end_gate=always→needs_confirmation | test_budget_hook.py / test_integrate_hook.py |
| CLI 退出码 | cli.main 断言 | 0/1/2/3/4/130；--json 字段 | test_cli.py |
| 子进程 spawn | demo 驱动 | cwd=模块目录落盘；FW_EXIT_INTERRUPT=1 → 130 → resume → 0 | test_subprocess_drivers.py |

## 8. 已知限制（与 README 一致，展开补充）

1. 真实 dsh preset 联动未验证（需求 3 轮 + 端到端轮）；本轮验收用确定性 inline 驱动 + demo 子进程代理。
2. `run_parallel` 是 ThreadPoolExecutor（本地等价物），非 dsh sessions.fork 本体；接口留适配点。
3. token 汇总未接 dsh token-meter（默认 0；fw-budget 轮接入后启用真 gate）。
4. 契约运行时校验/预测基线对照归 fw-integrate（本轮仅钩子）。
5. 中断批次内未完成模块不自动回滚；已完成永不重跑（快照语义）。
6. 心跳是"轮次级"守卫，不做单轮内进程挂死检测（属 dsh session 基础设施）。
7. REVIEW 机器键单一写者（runner）；driver 若写会覆盖。
8. 同批并行模块的事件在 dispatch.jsonl 中按完成序落盘（seq 保证单调，不保证模块间相对顺序稳定）。
9. 同一任务根重复从零 run 会旋转归档旧 dispatch（见 §5）；快照.json 始终单份（每 run 覆盖），如需保留历史快照应归档 `/总日志`。
9. `--executor-cmd/--auditor-cmd` 为 shell 模板；生产应使用绝对路径白名单（防注入），demo 默认内置。
