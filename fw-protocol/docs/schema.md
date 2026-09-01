# fw-protocol 任务书 schema 文档（v0.4）

> 定位：任务书（`task.yaml`）是 dsh 任务编排层的「共同语言」。planner 产出 → `fw-protocol` 校验 →
> scaffold 派生 → runner 拓扑调度 → integrate 集成对照，全部基于本 schema。
> 机器定义：`schema/task-schema.json`（JSON Schema draft 2020-12）；人类可读说明 = 本文档。

## 一、整体结构

```yaml
task:        # 任务元信息 + 预测基线
budget:      # 预算闸门配置（fw-budget 消费）
runtime:     # 执行期配置（fw-runner 消费）
modules:     # 模块清单（原子任务合同）—— 必填，至少 1 个
integration: # 集成验收配置（fw-integrate 消费）
```

`task` 与 `modules` 必填；`budget` / `runtime` / `integration` 可省略，程序校验时套用默认值
（见「三、默认值」）。所有对象默认 `additionalProperties: false` —— 拼写错误/未知字段会被校验器拦下，
避免"悄悄写错、下游踩雷"。

## 二、字段含义

### 2.1 task（任务元信息）

| 字段 | 类型 | 必填 | 默认 | 含义 |
|---|---|---|---|---|
| `name` | string | ✅ | – | 任务名（唯一；生成任务文件夹 `任务-<名>_<日期>` 用） |
| `source_prd` | string | – | – | PRD / 原始需求来源（路径或段号），溯源用 |
| `owner` | string | – | – | 任务负责人（人） |
| `created` | string | – | – | 创建日期，ISO 格式（建议加引号 `"2026-08-21"`，YAML 会隐式解析日期） |
| `grade` | enum `A/B/C` | – | – | 任务等级：A 大项目 / B 中小 / C 小修 |
| `prediction_baseline.will_have` | string[] | – | `[]` | 预测基线：最终交付会有什么（fw-integrate 对照） |
| `prediction_baseline.will_not_have` | string[] | – | `[]` | 预测基线：最终交付不会有什么 |

### 2.2 budget（预算闸门，fw-budget 消费）

| 字段 | 类型 | 必填 | 默认 | 含义 |
|---|---|---|---|---|
| `max_tokens` | int ≥1 | – | `1000000` | 全局 token 总预算 |
| `warn_at` | number 0–1 | – | `0.7` | 用量到该比例时**预警**（提示不停机，模块消耗排行） |
| `stop_at` | number 0–1 | – | `1.0` | 用量到该比例时**硬停**（goal pause + 快照 + 抛人） |
| `per_module_max_tokens` | int ≥1 | – | `= max_tokens` | 单模块 token 上限，防失控模块吃光全局；未设置 = 不单独限制 |

约束：`warn_at ≤ stop_at`（违反 → error `budget_range_invalid`）；
`per_module_max_tokens ≤ max_tokens`（违反 → warning `budget_per_module_gt_global`）

### 2.3 runtime（执行期配置，fw-runner 消费）

| 字段 | 类型 | 必填 | 默认 | 含义 |
|---|---|---|---|---|
| `models.planner` | string | – | `deepseek-v4-pro` | 规划层模型 |
| `models.executor` | string | – | `deepseek-v4-flash` | 执行层模型 |
| `models.auditor` | string | – | `deepseek-v4-flash` | 审计层模型（质量闸门，必要时可升档） |
| `max_parallel` | int ≥1 | – | `3` | 最大并行模块数（依赖图拓扑分层，同层并行 ≤ 此值） |
| `executor_max_rounds` | int ≥1 | – | `5` | 单模块 executor 轮数上限，超了 = 判定卡循环换人 |
| `retry_before_switch` | int ≥1 | – | `2` | auditor 打回 N 次后换 executor |
| `max_executor_switches` | int ≥0 | – | `1` | 最多换几个 executor，再卡回人 |
| `end_gate` | enum `auto/always` | – | `auto` | `auto`=异常才找人（lh 式）`always`=每任务人工确认 |

### 2.4 modules（模块清单）—— 必填，≥1 项

| 字段 | 类型 | 必填 | 默认 | 含义 |
|---|---|---|---|---|
| `id` | string `^m\d+$` | ✅ | – | 模块 id，规范 `m01`..`mNN`（依赖引用用） |
| `name` | string | ✅ | – | 模块名 |
| `layer` | int 1–3 | ✅ | – | 树深 ≤ 3：1=顶层模块，2/3=细化层；更深应换流水线 |
| `objective` | string | ✅ | – | 一句话职责（高内聚点） |
| `dependencies` | string[] | – | `[]` | 依赖的模块 id 列表（放弃环） |
| `interfaces[]` | object[] | – | `[]` | 接口协议，**只到 前缀+方法 级**（禁止规划期定字段） |
| `interfaces[].path` | string | ✅ | – | 接口路径前缀（如 `/api/order/*`） |
| `interfaces[].method` | string 或 string[] | ✅ | – | HTTP 方法，大小写不敏感（内部统一大写），可单可列表 |
| `interfaces[].note` | string | – | – | 一句话说明 |
| `acceptance` | string[] | ✅ | – | 模块级验收清单（必须可检查、可执行验证），≥1 条 |
| `boundaries` | string[] | – | `[]` | 边界：不许碰什么 |
| `round_estimate` | int ≥1 | – | – | 预估该模块需要多少 executor 轮（llm 回合）。未填不校验（向后兼容）；填了走「轮数预判自检」（见 4.5）：`> 上限` warning、`> 上限×2` error |
| `max_rounds_override` | int ≥1 | – | `= runtime.executor_max_rounds` | 该模块自己的 executor 轮数上限覆盖；缺省继承 runtime 值（默认 5） |

### 2.5 integration（集成验收配置，fw-integrate 消费）

| 字段 | 类型 | 必填 | 默认 | 含义 |
|---|---|---|---|---|
| `contract_file` | string | – | `contracts/api.yaml` | 契约区路径（所有接口集中管理，变更触发下游重验） |
| `check.dependency_cycle` | bool | – | `true` | 依赖环检测（**fw-protocol 实现**） |
| `check.interface_duplicate` | bool | – | `true` | 接口重复检测（**fw-protocol 实现**） |
| `check.acceptance_conflict` | bool | – | `true` | 验收冲突检测（**fw-protocol 实现**） |
| `check.prediction_baseline` | bool | – | `true` | 预测基线对照（fw-integrate 实现，本模块仅承载配置） |
| `check.cross_module_data_dependency` | bool | – | `true` | 跨模块数据依赖检查（fw-integrate 实现，本模块仅承载配置） |

> 说明：`prediction_baseline` 与 `cross_module_data_dependency` 两个开关在本模块（fw-protocol）
> 仅做 schema 承载，不由校验器执行；执行归属 fw-integrate（需求 6）。

## 三、默认值（程序套用，产出 effective 任务书）

校验器对缺省字段套默认值（深拷贝，不改原文件），返回的 `effective` 任务书供下游直接消费：

- budget：`max_tokens=1000000, warn_at=0.7, stop_at=1.0, per_module_max_tokens=max_tokens`
- runtime：`models={planner: deepseek-v4-pro, executor: deepseek-v4-flash, auditor: deepseek-v4-flash}`,
  `max_parallel=3, executor_max_rounds=5, retry_before_switch=2, max_executor_switches=1, end_gate=auto`
- 模块级：`dependencies=[], interfaces=[], boundaries=[]`，
  `round_estimate` 缺席（未预估，不注入 None——避免 scaffold 落盘后复校验失败），
  `max_rounds_override=runtime.executor_max_rounds`（继承补全）
- integration：`contract_file=contracts/api.yaml, check.*全部 true`

## 四、三查语义（校验器实现的行为）

### 4.1 依赖环检测（`dependency_cycle`）

- 实现：三色 DFS 枚举简单环，规范化去重（环旋转到最小节点开头、同集去重），上限 50 个。
- 命中 → `error`，`detail.cycle` 给出完整环路径，如 `["m01","m03","m02","m01"]`。
- 顺带四查：模块 id 唯一（`module_id_duplicate`）、依赖引用未定义模块（`dep_unknown_module`）、
  依赖列表重复条目（`dep_duplicate`）；自依赖按单节点环报。

### 4.2 接口重复检测（`interface_duplicate`）

- 判定 = 路径前缀**完全相同** 且 方法集合**有交集**（方法归一化大写：`post`==`POST`）。
- 命中 → `error`，`detail` 指出双方模块 id、冲突路径、共享方法。
- 同一模块内部声明重复同样报（职责内冲突）。
- **范围限制**：通配符"语义覆盖"（`/api/order/*` 覆盖 `/api/order/item`）**不做**重叠检测，
  只做精确前缀重复 —— 避免误报，留作已知限制。

### 4.3 验收冲突检测（`acceptance_conflict`）—— 需人工定优先级

- 对每个模块，扫 `name + objective + acceptance + boundaries` 文本；同时命中 **speed** 类与
  **safety** 类关键词 → 报 `conflict`（severity=conflict）。
- **关键属性：只标记、不代定优先级**（三权分立 / 人工拍板）。conflict 不算 error：
  `ok=True`，但 `status="conflict"`，CLI 退出码 `2` —— 编排层据此回人拍板。
- 默认关键词组（启发式，可整体替换，见 `fw_protocol/conflicts.py`）：
  - speed：`快 / 性能 / 提速 / 低延迟 / 低时延 / 响应快 / 尽快 / 赶进度 / 先上线 / 越快越好 / speed / fast / quick / performance / latency`
  - safety：`安全 / 稳定 / 可靠 / 万无一失 / 不出错 / 零出错 / 严谨 / 保守 / 慎重 / 宁慢勿错 / 质量优先 / 安全第一 / safety / secure / stable / reliable / robust / correctness`
- 误报只会多一次人工确认（方向安全）；漏报由 auditor 验收阶段人工兜底。
- 可用 `integration.check.acceptance_conflict: false` 或 CLI `--no-conflict` 关闭。

### 4.4 预算自检

- `warn_at > stop_at` → error（预警晚于硬停，配置矛盾）。
- `per_module_max_tokens > max_tokens` → warning（单模块上限失去约束意义）。

### 4.5 轮数预判自检（round_estimate）

规划期预判模块大小，避免 executor 运行时撞轮数上限白烧 token（真实案例：单模块 200 万+ token 反复重试）。
仅当模块**显式填了** `round_estimate` 时检查（未填 = 旧任务书，向后兼容不告警）：

| 条件 | 级别 | code | 处理 |
|---|---|---|---|
| `round_estimate < 1` | error | `schema` | 被 JSON Schema `minimum:1` 拦下 |
| `round_estimate > max_rounds_override` | warning | `module_round_estimate_over_cap` | 建议切开 |
| `round_estimate > max_rounds_override × 2` | error | `module_round_estimate_too_large` | **强制切开**（横向并行 A1/A2 或纵向串行 A1→A2） |

`max_rounds_override` 缺省继承 `runtime.executor_max_rounds`（默认 5），模块可单独提高/收紧。

## 五、CLI 用法与退出码（机器可解析）

```bash
# 进入模块目录后
./bin/fw-protocol examples/task-valid.yaml            # 人类可读
python3.11 -m fw_protocol.cli task.yaml --json        # 机器可读 JSON（含 effective）
python3.11 -m fw_protocol.cli task.yaml --no-cycle    # 临时关闭环检测
python3.11 -m fw_protocol.cli task.yaml --effective eff.json   # 导出补默认值后的任务书
```

| 退出码 | 含义 | 说明 |
|---|---|---|
| `0` | pass | 通过（无 error / conflict / warning） |
| `1` | error | 校验失败（结构错 / 依赖环 / 接口重复 / 预算矛盾） |
| `2` | conflict | 验收冲突，需人工定优先级（不算结构错误） |
| `3` | io/schema | 文件读不了或 schema 加载失败 |
| `4` | usage | CLI 用法错误 |

`--json` 输出结构：`{ok, status, errors[], conflicts[], warnings[], effective{}}`，
每条 issue：`{code, severity, message, module_id?, detail{}}`。

## 六、Python API

```python
from fw_protocol import validate_document, validate_file, Issue, ValidationResult

result = validate_file("task.yaml")          # 或 validate_document(dict)
result.status        # "pass" | "conflict" | "error"
result.ok            # bool（无 error）
result.errors        # tuple[Issue,...]  依赖环/接口重复/结构错 等
result.conflicts     # tuple[Issue,...] 验收冲突（需人工）
result.warnings      # tuple[Issue,...]
result.effective     # dict 补默认值后的任务书（给 scaffold/runner/integrate）

# 自定义冲突关键词组
result = validate_document(doc, groups={"cheap": ["省钱"], "premium": ["豪华"]})
```

## 七、示例

完整合法示例见 `examples/task-valid.yaml`（订单数据管道，3 模块依赖链 m01→m02→m03）。
反面教材：`examples/task-cycle.yaml`（环）、`examples/task-interface-dup.yaml`（接口撞车）、
`examples/task-conflict.yaml`（验收冲突）。

## 八、已知限制（如实标注）

1. **通配符接口不做语义覆盖检测**：只做精确前缀重复，`/api/order/*` 与 `/api/order/item` 不判重。
2. **验收冲突为启发式关键词**：可能误报/漏报；误报方向安全（多一次人工确认），漏报靠 auditor 兜底。
3. **关键词组默认值面向中文 + 英文驱动词**：行业黑话（如"毫秒级"）未全覆盖，可自定义 groups。
4. **`prediction_baseline` / `cross_module_data_dependency` 开关仅承载配置**，执行在 fw-integrate（需求 6）。
5. **结构错误时不跑语义三查**：依赖环等语义检查依赖结构成立，结构不合法先修结构。
6. **依赖环枚举上限 50 个**：异常病态图只报前 50 个环（正常任务书远低于此）。
7. **`round_estimate` 非必填字段**：为兼容旧任务书 schema 不强求，未填不触发轮数预判自检；
   强制填写靠 planner persona 铁律（见 presets/fw-planner）。
