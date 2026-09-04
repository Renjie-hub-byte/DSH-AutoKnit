# framework-v1 —— dsh 之上的任务编排层（v0.4 实现）

> PM（PM）｜ 2026-08-21 ｜ 状态：需求 1-7 全部落地（需求 1/2/3/4/5/6 经 auditor round_001/002/004/005/007/008 审计 complete/clean；需求 7 端到端 + 文档见 `e2e/` 与本文件）
> 定位：在 dsh（DeepSeek Harness / Cordis 插件平台）之上实现轻量**任务编排层**——规划共识 → 事件驱动分治执行，
> 把任务拆成模块、并行执行、独立验收、预算控制。比 某高星harness框架 更省 token（并行 + fork 继承 + 事件流增量而非全量重发）。

---

## 一、架构总览

```
┌────────────────────────────────────────────────────────────────────────────┐
│                          dsh 平台（Cordis 插件生态）                          │
│  沙箱硬隔离 / sessions.fork(省缓存) / fs 原子写(并发防护) / 事件 seq(完整性)   │
│  sessionProjections checkpoint / session-query(审计) / token-meter(记账)      │
└───────────────▲───────────────────────────────▲────────────────────────────┘
                │ 接入点（标注为适配点/本地等价物，见 design-v04.md §五）
┌───────────────┴───────────────────────────────┴────────────────────────────┐
│                          framework-v1 编排层（本仓库）                        │
│                                                                              │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐   ┌─────────────┐ │
│  │ fw-protocol  │───▶│ fw-scaffold  │───▶│  fw-runner   │──▶│fw-integrate │ │
│  │ 任务书schema  │    │ 目录脚手架 v2 │    │ 编排主循环     │   │ 集成验收+归档│ │
│  │ 三查校验器     │    │ 派生模块任务书 │    │ 并行/升级链/    │   │ 运行时契约校验│ │
│  │ (需求1)       │    │ (需求2)       │    │ checkpoint/   │   │ 基线对照     │ │
│  │              │    │              │    │ 心跳          │   │ (需求6)      │ │
│  └──────────────┘    └──────────────┘    └──────┬───────┘   └──────┬──────┘ │
│        │ CLI/API                │                 │  BudgetGate     │        │
│        ▼                        ▼                 ▼                 ▼        │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐   ┌─────────────┐ │
│  │ presets/     │    │  fw-budget   │    │ 总日志/       │   │  e2e/        │ │
│  │ 三角色 dsh    │    │ 预算闸门     │    │ event seq     │   │ 端到端示例   │ │
│  │ preset       │    │ warn/stop/   │    │ 快照.json v3  │   │ + 复现脚本   │ │
│  │ (需求3)      │    │ add-budget/  │    │ checkpoint    │   │ (需求7)      │ │
│  │              │    │ resume/归档   │    │              │   │              │ │
│  │              │    │ (需求5)      │    │              │   │              │ │
│  └──────────────┘    └──────────────┘    └──────────────┘   └─────────────┘ │
└────────────────────────────────────────────────────────────────────────────┘
```

**六模块职责一句话**：

| 模块 | 目录 | 职责 | 审计 |
|---|---|---|---|
| fw-protocol | `fw-protocol/` | task.yaml JSON Schema（draft 2020-12）+ 三查（依赖环 DFS / 接口前缀+方法重复 / 验收冲突标记） | round_001 ✅ |
| fw-scaffold | `fw-scaffold/` | 读合法 task.yaml 一键生成 v2 目录树 + 派生模块任务书 + 模板 + expected 版本防护 | round_002 ✅ |
| fw-runner | `fw-runner/` | 依赖图拓扑分层 → 并行调度 ≤max_parallel → 升级链（2+1 回人）→ 心跳 → checkpoint/resume | round_004 ✅ |
| fw-budget | `fw-budget/` | token 记账（dsh token-meter 适配点 stub）+ 70%/100%/单模块上限闸门 + add-budget/resume/归档 | round_005 ✅ |
| fw-integrate | `fw-integrate/` | 运行时契约校验（接口/数据格式/跨模块依赖）+ 预测基线对照 + end_gate + 完成报告/归档 | round_007 ✅ |
| presets | `presets/` | 三角色 dsh preset（planner/executor/auditor）+ 三权分立 persona + auditor 四段机器可解析输出 | round_008 ✅ |

## 二、全流程（一次任务的编排生命周期）

```
PLANNER(人/preset) ── 产出 task.yaml（只拆不写）
   │  fw-protocol 校验（结构 schema + 依赖环/接口重复/验收冲突三查）── 不通过 → 打回规划
   ▼
fw-scaffold 生成任务目录树（任务-<名>_<日期>/ + 总日志三件套 + modules/mXX-*/ + 派生任务书）
   ▼
fw-runner 主循环（本框架核心）：
   依赖图拓扑分层 → 同层独立模块并行（sessions.fork 继承公共上下文，≤ max_parallel）
   → 每模块：executor 干活（cwd=模块目录，sandbox workspace-write）→ auditor 判定（read-only）
   → 升级链：block → 回同 executor(附带 REVIEW.md 反馈) → block 满 retry_before_switch 次 → 换 executor(交接三件套)
              → 满 max_executor_switches → 回人；root∈{upstream,contract} 直接抛人不重试
   → 心跳守护：连续 N 轮无实质产出 → 静默卡死 → 进升级链
   → 每 checkpoint_every 模块完成 / 关键状态转移写 总日志/快照.json（schema v3，原子写）
   → 模块完成时挂 BudgetGate（warn 70% 提示不停机 / stop 100% 硬停抛人 / 单模块超限硬停）
   → 全部模块完成 → 挂 IntegrationHook（fw-integrate 运行时契约校验 + 基线对照）
   ▼
fw-integrate 收尾：end_gate=auto → 全部通过 → 完成报告.md + 归档 archived/；异常 → 回人
   ▼
fw-budget 管理：人工 add-budget → fw-runner --resume-from-checkpoint 续跑（已完成模块零重跑）；
                放弃 → fw-budget archive 归档
```

## 三、目录规范 v2（fw-scaffold 产物形态）

```
任务-<名>_<日期>/
├── task.yaml                # effective 任务书（默认值补全；唯一事实源）
├── contracts/api.yaml       # 契约区（所有接口集中管理）
├── skeleton.md 认知/  shared/ # 骨架 / 规划认知区 / 只读共享区（.readonly，非 auditor 豁免区）
├── 总日志/                   # dispatch.jsonl（事件 seq）+ integration.jsonl + 快照.json
└── modules/
    └── mXX-<名>/
        ├── src/ test/       # 交付物（auditor 结果对照面）
        ├── logs/ tmp/       # 豁免区（.auditor-ignore：执行期日志/临时文件）
        ├── REVIEW.md        # 模块验收闭环（机器键 status/executor_round/auditor_round/root/confidence…）
        ├── contract.yaml    # 模块接口契约（read_api 预填；input/output 由 executor 填写）
        ├── 任务书-mXX.yaml   # 派生模块级任务书（原子合同）
        └── 交付说明.md       # executor 交付报告（基线证据面）
```

## 四、全部配置项（task.yaml）

### 4.1 task（任务元信息 + 预测基线）

| 字段 | 类型 | 必填 | 默认 | 含义 |
|---|---|---|---|---|
| `task.name` | string | ✅ | – | 任务名（生成 `任务-<名>_<日期>` 目录） |
| `task.source_prd` | string | – | – | PRD 来源（溯源） |
| `task.owner` / `task.created` / `task.grade` | – | – | – | 负责人 / 创建日期 / 等级 A B C |
| `task.prediction_baseline.will_have` | string[] | – | `[]` | 预测基线：最终交付会有什么（integrate 对照） |
| `task.prediction_baseline.will_not_have` | string[] | – | `[]` | 预测基线：不会有什么（违反即回人） |

### 4.2 budget（预算闸门，fw-budget 消费）

| 字段 | 类型 | 必填 | 默认 | 含义 |
|---|---|---|---|---|
| `budget.max_tokens` | int ≥1 | – | `1000000` | 全局 token 总预算 |
| `budget.warn_at` | number 0–1 | – | `0.7` | 用量≥该比例 → **预警**（不停机，含模块消耗排行） |
| `budget.stop_at` | number 0–1 | – | `1.0` | 用量≥该比例 → **硬停**（goal pause + 快照 + 抛人） |
| `budget.per_module_max_tokens` | int ≥1 | – | `=max_tokens` | 单模块上限（防失控模块吃光全局；超限硬停） |

### 4.3 runtime（执行期配置，fw-runner 消费）

| 字段 | 类型 | 必填 | 默认 | 含义 |
|---|---|---|---|---|
| `runtime.models.planner/executor/auditor` | string | – | `deepseek-v4-pro / deepseek-v4-flash / deepseek-v4-flash` | 三角色模型 |
| `runtime.max_parallel` | int ≥1 | – | `3` | 最大并行模块数（同层并行 ≤ 此值） |
| `runtime.executor_max_rounds` | int ≥1 | – | `5` | 单模块 executor 轮数上限（超限换人/回人） |
| `runtime.retry_before_switch` | int ≥1 | – | `2` | auditor 打回 N 次后换 executor |
| `runtime.max_executor_switches` | int ≥0 | – | `1` | 最多换几个 executor，再卡回人 |
| `runtime.end_gate` | enum | – | `auto` | `auto`=异常才找人；`always`=每任务人工确认 |

### 4.4 modules（模块清单，≥1）

| 字段 | 类型 | 必填 | 默认 | 含义 |
|---|---|---|---|---|
| `id` | `^m\d+$` | ✅ | – | 模块 id（依赖引用） |
| `name` / `objective` / `layer`(1-3) | – | ✅ | – | 模块名 / 一句话职责 / 树深 |
| `dependencies` | string[] | – | `[]` | 依赖模块 id（禁止环） |
| `interfaces[]` | object[] | – | `[]` | 接口协议（**只到 前缀+方法 级**，禁规划期定字段） |
| `interfaces[].path` / `.method` / `.note` | – | ✅/✅/– | – | 路径前缀（`/api/order/*`）/ HTTP 方法（大小写不敏感）/ 说明 |
| `acceptance` | string[] | ✅ | – | 模块验收清单（≥1，必须可检查） |
| `boundaries` | string[] | – | `[]` | 边界：不许碰什么 |

### 4.5 integration（集成验收，fw-integrate 消费）

| 字段 | 类型 | 必填 | 默认 | 含义 |
|---|---|---|---|---|
| `integration.contract_file` | string | – | `contracts/api.yaml` | 契约区路径 |
| `integration.check.*`（5 个 bool） | bool | – | 全 `true` | 依赖环/接口重复/验收冲突（fw-protocol 执行）＋预测基线/跨模块数据依赖（fw-integrate 执行） |

### 4.6 runner CLI 覆盖参数（`fw-runner run <根> [--max-parallel N --executor-max-rounds N --retry-before-switch N --max-executor-switches N --end-gate auto|always --heartbeat-n-rounds N --checkpoint-every N --mode speed_first|cost_first --resume-from-checkpoint]`）

## 五、模式开关（speed_first / cost_first）

| 模式 | 语义 | 差异 |
|---|---|---|
| `speed_first`（默认） | 追求吞吐 | `max_parallel` 用 runtime 原值；最大并行 |
| `cost_first` | 省 token / 会话 | `max_parallel` 压到 `min(max_parallel, 2)`；`retry_before_switch + 1`（提高同 executor 打回耐心，少换人少开销） |

显式 CLI 覆盖优先级最高，不受模式影响。端到端示例默认 `speed_first`（见 `e2e/task.yaml` runtime 段）。

### 5.1 拆分驱动模式（FW_SPLIT_MODE，2026-09-04 约定）

| 模式 | 语义 | 适用 |
|---|---|---|
| `dsh`（**生产默认**） | split agent 真调 flash 模型拆解（一次性，timeout 300s） | 正式任务运行 |
| `demo` | 写最小可用拆解 JSON，不调任何模型 | 单元测试、链路联调 |

- **框架现有测试已全部通过驱动注入隔离，不依赖该默认值**（`split_driver` 参数注入）；
  但**新写端到端/验收测试时必须显式 `FW_SPLIT_MODE=demo`**（或经 `split_driver` 注入），
  否则会真打模型（timeout 300s + 真 token 消耗）。
- 需要在测试里允许真调用的场景，显式声明 `FW_SPLIT_MODE=dsh`，不要依赖默认值。

## 六、升级链与预算闸门速查

### 6.0 安全边界（威胁模型，2026-09-04）

- **信任边界**：能写任务目录（`modules/*/`、`总日志/`）的主体 = 能在 executor/auditor
  执行期引入任意内容（LLM 会话会读取 REVIEW/产物清单——存在 prompt 注入面）。
  框架以「任务目录内容可信」为前提，适用于自用/受控 CI；开源部署建议任务目录仅限可信方写入。
- **命令执行**：drivers 的 cmd 模板占位值已 `shlex.quote`（M5，防御性）；当前占位值经
  环境变量传递，不经 shell 二次解析。新增 cmd 模板时占位符应独立成词，勿嵌路径中段。

## 六、升级链与预算闸门速查

- **升级链（2+1 回人）**：auditor block → 回同 executor（REVIEW.md 已附判定）→ 满 `retry_before_switch` 次 → 换新 executor（交接三件套 = REVIEW.md + contract.yaml + 交付说明.md，写 `logs/handover-*.md`）→ 满 `max_executor_switches` → 回人；**失败根因分流**：`root ∈ {upstream, contract}` 直接抛人不重试；心跳卡死（连续 N 轮无实质产出）等效 block/self 但不给 retry。
- **预算闸门**：token 记账 = 每轮 `DriverOutcome.tokens`（dsh token-meter 对接钩子，见 `fw-budget/fw_budget/meter.py` 适配点）；70% warn（事件 `budget.warn` + 排行）、100% stop（事件 `budget.stop` + 快照 status=stopped + 抛人信息完备）、单模块超限硬停。
- **加预算续跑**：`fw-budget add-budget <根> <新上限> --reason ...`（fs 原子写）→ `fw-budget resume <根>`（历史消耗灌回 BudgetGate）→ 已完成模块零重跑（快照 checkpoint）。放弃 → `fw-budget archive <根>`（拒绝续跑）。

## 七、快速开始（含端到端复现）

```bash
# 环境：Python 3.11 + PyYAML + jsonschema（已在 dsh 环境）
cd ~/projects-hold/projects/dsh-workflow/framework-v1

# 单模块用法示例（以 fw-scaffold 示例任务书为例；用 fw-integrate 的 conform 驱动交付，
# 保证产物与基线匹配，全链跑通；默认 demo 驱动只完成编排、集成会按基线缺失正确回人——
# 这正是"运行时契约校验"的预期行为）
FW1=~/projects-hold/projects/dsh-workflow/framework-v1
X=/tmp/fw1-mydemo
python3.11 $FW1/fw-protocol/bin/fw-protocol $FW1/fw-scaffold/examples/task-valid.yaml --json   # ① 校验
python3.11 $FW1/fw-scaffold/bin/fw-scaffold $FW1/fw-scaffold/examples/task-valid.yaml -o $X      # ② 生成
python3.11 $FW1/fw-runner/bin/fw-runner run $X/任务-示例-订单管道_2026-08-21 \
  --executor-cmd "python3.11 $FW1/fw-integrate/examples/executor-conform.py" \
  --auditor-cmd  "python3.11 $FW1/fw-integrate/examples/auditor-conform.py" --json              # ③ 编排
python3.11 $FW1/fw-integrate/bin/fw-integrate complete $X/任务-示例-订单管道_2026-08-21 --json   # ④ 集成归档

# 需求7 端到端示例（3 模块，含 1 次失败升级链路 + 预算 warn 路径 + 集成归档）
python3.11 e2e/run_e2e.py            # 每次独立运行目录 e2e/runs/run-<ts>/，可重复执行
python3.11 e2e/run_e2e.py --json     # 机器可解析摘要
# 证据链：e2e/runs/run-<ts>/e2e-evidence.md + runner-result.json + budget-report.json
#        + archived/任务-端到端-订单管道_*/（完成报告.md + ARCHIVE.md + 快照/事件流/REVIEW/handover）
```

端到端示例已实测全部通过（详见 `e2e/README.md` 与 `e2e/runs/` 下的真实运行产物）。

## 八、已知限制（诚实交付）

1. **dsh token-meter 为适配点 stub**：token 汇总默认走本地事件流账本（`EventLogTokenMeter`，从 `总日志/dispatch.jsonl` 归集每轮 `DriverOutcome.tokens`）；真实 `dsh meter` 接入点见 `fw-budget/fw_budget/meter.py` 的 `DshTokenMeter._query_dsh()`（当前恒返回 None → fallback）。
2. **dsh 真实能力接入点标注**：沙箱硬隔离 / sessions.fork / sessionProjections checkpoint / session-query / 事件 seq 在框架内以"本地等价物 + 适配点"落地（如 fork.py、checkpoint.py、events.py、io_utils.py），未在真实 dsh 平台跑通——文档（design-v04.md §五）逐一标注了接入语义。
3. **三 preset 在 dsh GUI 可选性未实测**：presets/ 提供 `preset.yml + agent.cordis.yml` 与挂载步骤（`cp -r presets/<角色> ~/.dsh/.agent-presets/` 后重启 GUI），真实 GUI 确认属收尾流程。
4. **验收冲突只标记不代定优先级**：命中"快 vs 安全"关键词 → conflict 上抛回人拍板（三权分立铁律：executor 永不自定验收标准）。
5. **契约为 前缀+方法 级**：规划期禁止硬定字段；字段级 schema 校验不在契约内（output.describe 为自然语言，数据格式校验是"解析级"）。
6. **预测基线对照为关键词启发式**：文件样 token 按路径存在性 + 中文/英文关键词在交付物文本搜索；证据面排除任务书回显（避免误命中），`will_not_have` 护栏语义宁多勿漏。
7. **单模块超限用硬停而非升级链**（v0.4 设计为进升级链，本实现按 round_005 文档标注为硬停——防失控模块吃光全局优先）。
8. **fw-scaffold manifest 不监控执行期**：目录生成后执行的产物变化不受 expected 版本防护管控（它只管生成期）。
9. **快照/事件流是 JSONL 与 JSON 文件**，规模大时可用 dsh 底部能力替代（当前直接读文件足够）。
10. **git 仓库与 GitHub 推送未执行**：项目根暂无 git 仓库，`git init/commit/push`（走 socks5h://127.0.0.1:1080 代理）属收尾流程，步骤见 design-v04.md §六。
11. **auditor persona 示例行措辞瑕疵**（round_008 遗留收尾项）：示例行 `verdict=complete` 与协议 `pass|block` 不一致，不影响 canonical JSON 契约与机器解析（`DriverOutcome.from_mapping` 白盒消费已验）。

## 九、文档索引

| 文档 | 位置 |
|---|---|
| 本文件（架构/流程/配置/模式开关/限制） | `framework-v1/README.md` |
| v0.4 设计实现度对照 | `framework-v1/design-v04.md` |
| 端到端示例复现指南 | `framework-v1/e2e/README.md` |
| 各模块详细规格 | `fw-protocol/docs/schema.md`、`fw-scaffold/docs/scaffold-spec.md`、`fw-runner/docs/runner-spec.md`、`fw-budget/docs/budget-spec.md`、`fw-integrate/docs/integrate-spec.md`、`presets/docs/presets-spec.md` |
