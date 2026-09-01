# fw-integrate 规范（需求 6：集成验收）

> 版本 v1.0 ｜ 状态：实现完成 ｜ 依赖：fw-protocol(round_001) / fw-scaffold(round_002) /
> fw-runner(round_004) / fw-budget(round_005) 均已审计通过，本模块只读消费、零源码修改。

## 1. 目标与边界

模块合流后把「集成验收」从人工比对升级为**程序化运行时契约校验**：

1. 读取契约区 `contracts/api.yaml`（fw-scaffold 生成）与各模块 `contract.yaml`（executor 执行期
   填写），对模块产物做**接口匹配**与**数据格式**运行时校验。
2. **跨模块数据依赖检查**：B 需要的输入（`input.from`）是否 A 的 output 声明过（`output.artifacts`
   非空且产物真实存在）。
3. **预测基线对照**：`task.prediction_baseline.will_have / will_not_have` 与真实交付物对照，
   输出**匹配/缺失清单**。
4. **end_gate 分流**：`auto`=异常才找人；`always`=全通过也需人工确认
   （fw-runner 停在 needs_confirmation，由 `fw-integrate confirm` 收口）。
5. 全部通过 → **完成报告 + 归档**（复用 fw-budget 归档机制）。

边界（本模块不做）：不做模块级验收（那是 fw-runner auditor 的职责）；不做预算控制（fw-budget）；
不改已审计四模块源码。

## 2. 检查项定义

### 2.1 接口匹配（check_interfaces）

契约区为权威基线（规划期 fw-protocol 已做接口重复检测；这里是**运行时侧**复检）：

| 发现 kind | severity | 语义 |
| --- | --- | --- |
| `contract_vs_module_missing` | error | 契约区登记给模块 X 的 (path, method)，X/contract.yaml read_api 未声明（运行丢失接口声明） |
| `cross_module_duplicate` | error | 模块 X 的 read_api 声明了登记给/已由模块 Y 声明的 (path, method) —— **同时点名 X 与 Y**（验收 1） |
| `unregistered` | warning | X 的 read_api 声明契约区未登记、其它模块也没有的接口 |
| （无发现） | - | 通过 |

方法归一化：method 统一转大写集合（`GET` / `["post","put"]` → `{GET,POST,PUT}`）。

### 2.2 数据格式（check_data_format）

各模块 `output.artifacts` 声明的每一条相对路径：

| 发现 kind | severity | 语义 |
| --- | --- | --- |
| `no_artifacts` | info | 模块未声明产物（只读/无产物模块可忽略） |
| `artifact_missing` | error | 声明产物在模块目录下不存在 |
| `artifact_format_error` | error | 产物存在但解析失败（JSON/YAML/CSV） |
| `artifact_ok` | info | 产物存在且格式解析通过（或扩展名不做解析、仅存在性） |

解析级校验：`.json`→`json.loads`；`.yaml/.yml`→`yaml.safe_load`；`.csv`→`csv.reader`（≥1 行）；
`.txt/.md`→UTF-8 文本；其它扩展名 → 仅存在性（`format_ok=None`，诚实标注）。

### 2.3 跨模块数据依赖（check_data_dependency）

读各模块 `input.from`：

| 条目形态 | 判定 |
| --- | --- |
| `mXX`（模块 id） | 对应模块 `output.artifacts` 非空且产物存在 → 满足；未声明 → error（**B 需要 A 的输入，A 的 output 未声明过**）；生产方产物缺失 → error |
| `shared/...` | 文件存在于任务根 shared/ → 满足，否则 error |
| `mXX/...` | 在 modules/<模块目录> 下解析 → 存在性判定 |
| 其它相对路径 | 依次在 shared/ 与任务根下解析 |
| 未知模块 id | warning |
| 任务书依赖未在 input.from 声明消费 | warning（可能为排序/间接依赖或未填报，提示性） |

### 2.4 预测基线对照（check_baseline）

见 §3（证据面口径）与 §4（关键词策略）。输出：

```
baseline.matched     will_have 命中清单（带证据路径）
baseline.missing     will_have 缺失清单
baseline.clean       will_not_have 未命中清单
baseline.violations  will_not_have 违反清单（含证据）
```

## 3. 证据面口径（重要）

预测基线只对 **executor 真实产出** 做证据匹配，**排除**任务输入回显与模板文件，防误命中：

| 入选（证据） | 排除（非证据） |
| --- | --- |
| `modules/*/src/**`、`modules/*/test/**` 文件（路径 + 文本） | `skeleton.md`（回显基线文本） |
| `modules/*/交付说明.md`（executor 撰写的交付说明） | `contracts/api.yaml`（回显接口声明） |
| `modules/*/REVIEW.md` **已做节**（executor 记录） | `module/contract.yaml`、`任务书-*.yaml`（模板注释含示例路径如 src/data/orders.json） |
| `shared/**` 非样板文件（executor/上游写入的数据） | `认知/`、`shared/README.md`、`.readonly`、`.gitkeep` |
| — | `logs/`、`tmp/`、`.auditor-ignore`（豁免区） |

文本读取：UTF-8，每文件 ≤8KB。

## 4. 关键词策略（启发式，诚实标注）

- **will_have**：文件样 token（含 `.` 或 `/`，如 `src/data/orders.json`）+ 中文连续片段（≥2 字）
  作为匹配关键词；**不用**自由 ascii 词（`json`/`src`/`yaml` 等易误命中，如"contract.yaml"里的
  yaml）。纯英文基线（无文件样无中文）回退 ascii 长词（≥5 且非噪声词）。
- **will_not_have**：文件样 token + 中文片段 + **中文安全二元组**（2 字窗，剔除停用字 不做与和及或
  为是的要应该有无处理本任务…）+ ascii（非噪声）。护栏语义：宁可多报（违反→上抛人工，安全方向）。
- 命中判定：任一关键词在证据路径或证据文本中出现即计入；matched 项带证据路径清单（最多 8 条）。

## 5. end_gate 分流与归档

| 场景 | 行为 |
| --- | --- |
| 检查有 error | 不归档，exit 2 抛人（错误清单含"哪两个模块"） |
| 全通过 & end_gate=auto | 追加 integration.check 事件 → 调 `fw_budget.manage.archive`（快照原子标记 + move 到 archived/ + ARCHIVE.md）→ 归档树内快照 cause 回写 `completed` → 写 `完成报告.md` → exit 0 |
| 全通过 & end_gate=always | runner 先停（快照 needs_confirmation，exit 2）；`fw-integrate confirm` 人工确认 → 完成报告 + 归档（status=confirmed，exit 0） |
| 全通过 & 快照已是 complete & end_gate=always（手工形态） | complete_and_archive 只写完成报告（status=needs_confirmation），不自动归档，exit 2 等人工确认 |
| 快照非 complete/needs_confirmation（未跑完） | complete/confirm 前置拒绝（exit 1），防未跑完就归档 |

归档后任务不可 resume（fw-budget 语义：archived 任务续跑被拒）。

**钩子只判不归档**：FwIntegrateHook 挂在 fw-runner 时只返回 passed/failed；归档必须在
`complete`/`run` 收尾阶段执行（runner 钩子返回后仍会写快照与 integration 日志，钩子内移目录
会破坏后续写 —— 已在 hook.py / archive.py 双处文档化）。

## 6. integration.jsonl 事件

`fw-integrate complete` 与 `fw-integrate check` 向 `总日志/integration.jsonl` 追加
`integration.check` 事件（与 fw-runner 同构）：

```json
{"ts":"...","seq":21,"run_id":"...","event":"integration.check","end_gate":"auto",
 "detail":{"status":"passed","ok":true,"errors":[],"warnings":[],"summary":{...}}}
```

seq 从 integration.jsonl 既有事件最大值续接（独立于 dispatch.jsonl 的 seq 域，文档化）。

## 7. 退出码（机器可解析）

| 码 | 含义 |
| --- | --- |
| 0 | check 通过 / complete 完成归档 / confirm 人工确认归档 |
| 1 | input_error（任务根/契约/快照状态不符合收口前置） |
| 2 | needs_human（集成失败 / end_gate=always 待确认） |
| 3 | io_error |
| 4 | usage |

## 8. 已知限制（诚实标注）

1. **字段级 schema 校验不做**：`output.describe` 是自然语言；格式校验是"解析级"（JSON/YAML/CSV
   可解析、存在性），不做字段结构强校验（规划期禁止硬定字段）。需字段级校验时放入
   `will_have` 用预测基线表达。
2. **预测基线是关键词启发式**：不做中文分词；will_have 用完整短语匹配，短语内词语序变化的
   交付文本可能漏报（missing）；will_not_have 用"中文安全二元组"抓部分命中（如"支付"），
   可能存在少量误报（方向安全：多报→上抛人工）。证据面口径见 §3。
3. **模板回显防误命中已处理但仍可能漏网**：若 executor 在交付说明里复述基线原句，will_have
   会误判为 matched（交付说明是 executor 真实产出，属证据面）。
4. **read_api 只校验声明一致性**：认定模块"只读暴露 API"（GET 系）与契约区一致；对 POST/PUT
   写入接口，模块间"谁声明谁暴露"以契约区为准，运行时真实报文是否到达不做网络验证（沙箱内
   无真实网关；如需可在 will_have 用产物路径表达）。
5. **dsh 真实能力为适配点**：session-query 查询各模块契约快照/产物清单、sessionProjections
   持久化验收状态 —— 本模块以文件系统读取为本地等价物；`IntegrateContext` 加载器接口可替换。
   token-meter 记账不属本模块（fw-budget 已处理）。
6. **integration.jsonl 合并写无锁**：同目录原子追加依赖单进程（runner 串行写）；多进程并发写
   需任务管理器串行（与 fw-runner 相同口径，未上 Redis/外部锁）。
7. **归档复用 fw-budget 机制的语义修正**：fw-budget archive 面向"放弃"场景（快照 cause=
   budget_abandoned）；完成归档复用其 move/ARCHIVE.md/原子标记机制，并在归档树内回写
   cause=completed。若未来 fw-budget 增加完成归档专用入口，本模块应切换（标注 TODO）。
8. **CLI run 默认驱动是演示脚本**：`fw-integrate run` 默认用 fw-runner 的 demo executor/auditor
   （不按契约交付 → 集成失败是预期）；真实使用需传 `--executor-cmd/--auditor-cmd`（示例见
   examples/executor-conform.py）或用 fw-runner 自带驱动。
9. **completed 判定以快照为准**：模块全 done 才允许归档；单模块 REVIEW status=done 但不进
   completed_order 的异常状态按"未完成"处理（快照为准）。
10. **跨沙箱校验不做**：整合期不重建沙箱；产物以文件形态在同一任务根内校验（dsh 沙箱硬隔离
    本任务内各模块产物互不可写的边界由 runner 的 workspace-write 保证，集成阶段只读）。
11. **end_gate=always 收口在 confirm**：fw-runner（已审计）在 always 门时把快照写成
    needs_confirmation 而非 complete，因此 fw-integrate complete 不适用于 always 任务；
    必须走 `fw-integrate confirm`（人工确认 → 完成报告 + 归档）。complete 与 confirm 的
    快照前置不同（complete：complete；confirm：needs_confirmation|complete）。

## 9. 与已审计模块的契约复用

| 复用点 | 出处（round） | 本模块用法 |
| --- | --- | --- |
| `validate_file().effective` | fw-protocol（001） | task/budget/runtime/integration/prediction_baseline 语义输入 |
| v2 目录树 + contracts/api.yaml + contract.yaml + 交付说明.md | fw-scaffold（002） | 集成输入的物理来源 |
| IntegrationHook + integration.check 事件 + 快照 schema v3 | fw-runner（004） | FwIntegrateHook、事件追加、快照读取/回写 |
| archive 机制 + 归档拒续跑 | fw-budget（005） | `fw_budget.manage.archive` 直接调用 |
