# AutoKnit CLI 参考

> 入口 `autoknit`（前身 `fw`，`autoknit X` ≡ `fw-X`）。所有子命令均可 `--help`。

## 命令一览

| 命令 | 意义 |
|---|---|
| `autoknit new <PRD.md>` | 从 PRD 规划任务（planner 真身跑，产出 task.yaml） |
| `autoknit run <任务目录>` | 全流程执行（拆/派/写/验/续做/递归） |
| `autoknit plan-only <任务目录>` | 只跑 planner：产出 task.yaml 供人审，不执行 |
| `autoknit status <任务目录>` | 实时状态（事件驱动跟随，`--once` 看一次） |
| `autoknit token [关键字]` | token 账（输入/输出/缓存命中/计费） |
| `autoknit doctor` | 环境体检：缺什么、怎么装 |
| `autoknit dashboard` | 数据桥面板（:8765 健康 + 最近 run） |
| `autoknit demo <任务目录>` | 链路联调（0 token，内置假 executor/auditor） |
| `autoknit summary <任务目录>` | 规划摘要（plan-only 同源） |
| `autoknit merge <任务目录>` | 程序化合代码（纯程序零 LLM，产出合并说明） |

## autoknit new / plan-only

```bash
autoknit new PRD.md [--name 任务名] [--owner 负责人] [--out 输出目录]
autoknit plan-only <任务目录>
```

- `new`：planner（dsh headless）读 PRD → 产出规划 JSON → fw-normalize 程序接管结构 → fw-scaffold 生成任务目录树。
- `plan-only`：同源规划但不执行，供人审模块拆解 / 契约清单 / 行数预估。
- 校验失败（接口重复 / 依赖环等）自动喂回 planner 修正，最多重试 2 次。

## autoknit run

```bash
autoknit run <任务目录> [--max-parallel N] [--executor-model M] [--auditor-model M] [--config dflow.yaml] [--resume]
```

### 阈值语义：`split_exit_threshold`（默认 1000 行）

剩余体量是拆分的核心决策点：

- **剩余 > 阈值** → split 递归拆分新块（每块小上下文更聚焦）
- **剩余 ≤ 阈值** → 当前 executor 续做收官（final block，上下文还热着，省 token 且质量连贯）

```
首发块(600行) → auditor pass → 剩余(400行) ≤ 1000 → final_block 收官 → done
```

> 这就是"每模块 2 轮"的正常路径：首发验收通过 + 收官续做，**不是打回重做**。看 `总日志/dispatch.jsonl` 里 `module.final_block` 事件的 `remaining ≤ threshold` 即确认。

### resume 机制

- `--resume` 从 checkpoint 接续（崩溃/中断后不重复规划、不重跑已完成模块）。
- 幂等：快照里 `done` 的模块跳过，中断恢复实测通过。
- 进行中的 run 时间窗回退用注册表 `started_at`，避免 `--since` 过滤失效串历史会话。

### 模型配置优先级

`dflow.yaml`（项目级）< 环境变量 < CLI flag。

```yaml
# dflow.yaml 示例
runtime:
  max_parallel: 2
  split_exit_threshold: 1000
models:
  executor:
    provider: deepseek-official
    model: deepseek-v4-flash
    reasoning_effort: low
  auditor:
    provider: deepseek-official
    model: deepseek-v4-flash
    reasoning_effort: low
```

## autoknit token（口径声明）

```bash
autoknit token                 # 所有 framework 相关会话
autoknit token <任务名/关键字>  # 只统计含关键字的会话
autoknit token --json [--since 毫秒] [--cwd 任务目录]   # 机器可读
```

| 字段 | 含义 |
|---|---|
| 输入 / 输出 | 未缓存输入 / 输出 token（fw 的 inputTokens 不含缓存） |
| 缓存读 | cacheReadTokens（单独上报，不计入计费） |
| **计费** | **未缓存输入 + 输出**（口径统一，三方可比） |

> 数据源：`$DSH_HOME/sessions/*/session-*/session.jsonl.zstd` 里 provider 上报的 usage。
> `--cwd` 限定会话必须落在某 run 任务目录下（防跨 run 同名模块串聚合，BUG-009）。

## autoknit doctor

```bash
autoknit doctor [--json]
```

检查 6 项（全部只读）：

| 项 | 检查内容 | 不过怎么办 |
|---|---|---|
| python | 3.11+ 含 pyyaml | `pip install pyyaml` |
| dsh | dsh 二进制 | 见 quickstart.md 附录 A |
| credentials | DEEPSEEK_API_KEY 或 dsh 凭据 | `dsh login` 或设环境变量 |
| model | settings.yaml 模型路由 | 配置 `~/.fw-dsh/settings.yaml` |
| bridge | 数据桥 :8765 | `autoknit run` 自动拉起，或 kickstart LaunchAgent |
| caffeinate | 防睡眠（macOS） | 系统自带，一般无需处理 |

## autoknit dashboard

```bash
autoknit dashboard [--limit N] [--json]
```

数据桥只读聚合：最近 N 个 run 的状态 + token 明细（计费 = 未缓存输入 + 输出，缓存单列）。

## autoknit merge

纯程序零 LLM 的机械合并：产出①按依赖拓扑归位的目录骨架 ②每个模块的目标接口文件 ③跨模块 import 接线 ④四类冲突清单（同名/命名不一致/接口签名出入/需语义融合——每条标注"需人工定夺"）。

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 成功 / 全部模块完成 |
| 1 | 参数错误 / 未知子命令 |
| 2 | 环境预检失败（缺 dsh 二进制 / 缺 DSH_HOME 凭据） |
| 非 0 | 执行失败（看 dispatch.jsonl 最后一条 = 谁崩的） |
