# fw-scaffold 目录规范 v2 说明（design spec）

> 读者：auditor / runner / integrate / 后续维护者。本文定义 fw-scaffold 生成目录树的
> 精确语义、每个文件的作用与机器接口、expected 版本防护机制、以及已知限制。
> 依据：任务书 v0.4 需求 2（目录规范 v2）+ 执行配置 9.6/9.7 + 千问评审 10.2（contract.yaml）。

## 一、输入输出契约

- **输入**：`task.yaml`，必须通过 fw-protocol `validate_file()` 校验（`errors` 为空）。
  - 结构错误/依赖环/接口重复/预算矛盾 → `TaskInvalidError`，拒绝生成（CLI exit 1）。
  - `conflicts`（快 vs 安全类，需人工定优先级）**不阻塞生成**——目录结构与优先级无关，
    冲突随 `ScaffoldResult.conflicts` 与 CLI 输出上抛（CLI exit 0 但醒目提示）。
- **输出**：`<output>/任务-<名>_<日期>/`（日期 = `task.created` 的 YYYY-MM-DD，缺省用当天）。

## 二、目录树（v2）逐项语义

| 路径 | 作用 | 谁写 | 机器接口 |
|---|---|---|---|
| `task.yaml` | effective 版本总任务书（默认值补全） | scaffold 派生 | runner/integrate 唯一事实源 |
| `contracts/api.yaml` | 全部模块接口汇总 | scaffold 派生 | fw-integrate 接口匹配基线；`api: [{module,path,method,note}]` |
| `skeleton.md` | 骨架说明（按 layer 分组 + 依赖链 + 集成配置 + 全局不会做清单） | scaffold 派生 | 无（人读） |
| `认知/` | 规划认知区（调研/拆解依据/滚动纪要） | planner | 无 |
| `shared/` | **只读共享区**：`README.md`（只读规则）+ `.readonly`（机器标记） | 无（只读） | `.readonly` 供 sandbox/auditor 识别；**不是豁免区** |
| `总日志/dispatch.jsonl` | 调度事件日志（scaffold 初始化一条） | scaffold 初始化 / runner 追加 | JSONL，逐行 JSON |
| `总日志/integration.jsonl` | 集成验收日志 | scaffold 初始化 / integrate 追加 | JSONL |
| `总日志/快照.json` | checkpoint 初始状态（status=scaffolded / modules=pending / 依赖图 / failure_counts=0） | scaffold 初始化 / runner 更新 | `{modules:{id:状态}, dependencies:{}, failure_counts:{}}` |
| `modules/mXX-<名>/src/` | 模块产物（代码） | executor | `.gitkeep` 占位 |
| `modules/mXX-<名>/test/` | 模块测试 | executor | `.gitkeep` 占位 |
| `modules/mXX-<名>/logs/` | 模块执行期日志 | executor/auditor | `.auditor-ignore`（豁免区标记） |
| `modules/mXX-<名>/tmp/` | 临时/中间产物（可随时清空） | executor | `.auditor-ignore`（豁免区标记） |
| `modules/mXX-<名>/REVIEW.md` | 验收闭环：status/executor_round/auditor_round/root/confidence 键值行 + 待办/已做/交接 | executor+auditor | 键值行机器可解析（runner 升级链读 status/root） |
| `modules/mXX-<名>/contract.yaml` | 接口契约：input/output 占位 + read_api 预填总任务书接口 | executor 填 input/output | fw-integrate 运行时契约校验 |
| `modules/mXX-<名>/任务书-mXX.yaml` | 派生模块任务书（原子合同） | scaffold 派生 | 字段与总任务书逐字段一致（见下） |
| `modules/mXX-<名>/交付说明.md` | 交付报告（改了什么/测了什么/风险） | executor | 无（人读 + auditor 佐证） |
| `.scaffold-manifest.json` | 版本守卫：scaffold_version/schema_version/task_fingerprint/全部文件 sha256 | scaffold | guard 比对依据 |
| `.scaffold-version` | `fw-scaffold/<版本>` + `目录规范v2` | scaffold | 人读版本标记 |

## 三、shared/ 与 tmp/ 的区分（验收 3 的语义）

- **shared/**：任务级**只读共享区**，跨模块读不写。写入需走 复制→改→合并→集成验收。
  机器标记 `.readonly`；**不属于 auditor 豁免区**（变更会触发审计）——与 logs/tmp 相反。
- **tmp/ + logs/**：每个模块下的**执行期豁免区**，标记 `.auditor-ignore`：auditor 过程审计与
  结果核对跳过此处（不属于交付物校验范围）；tmp 可随时清空。
- 一句话：`shared/` = 只读共享（受审计），`logs/+tmp/` = 豁免区（审计忽略）；`src/+test/` = 交付物。

## 四、派生模块任务书（原子合同）语义

- 实现：`derive.derive_module_book` = 深拷贝 effective → `modules` 只留本模块。
- **字段齐全**：`task/budget/runtime/integration` 与 `id/name/layer/objective/dependencies/
  interfaces/acceptance/boundaries` 全量保留（YAML 注释头除外无增删改）。
- **语义一致**：测试逐字段比对（`test_derived_book_fields_complete_and_consistent`）。
- **上下文注释**（在 YAML 注释头，非数据字段）：upstream（输入来源）、downstream（依赖本模块的模块）。
- **已知限制**：派生书是子集，dependencies 可能引用外部模块 → fw-protocol 直接校验报
  `dep_unknown_module` 属预期；语义一致性由比对测试保证，不做"单书独立可校验"承诺。

## 五、原子写与 expected 版本防护

1. `atomic_write_text`：同目录 `tempfile.mkstemp` → 写 UTF-8（`\n` 归一）→ flush+fsync → `os.replace`
   （POSIX rename 原子替换）。不引入锁；目录内 rename 天然串行。
2. 生成完成后 `write_guard_manifest` 写 `.scaffold-manifest.json`（task_fingerprint + 全部文件 sha256）
   与 `.scaffold-version`。
3. `guard_existing_dir` 路径判定（`--force` 可越过所有拒绝分支）：
   - fresh：目录不存在/为空 → 全新生成。
   - idempotent：manifest 存在且 task 指纹与全部文件 hash 一致 → 重写相同字节，幂等。
   - 拒绝（ExpectedVersionMismatch，CLI exit 2）：
     a. manifest 存在但 task_fingerprint 与本次将写入的 task.yaml 不一致（目录是另一份任务书/同名异内容）；
     b. manifest 存在但某生成文件 hash 与记录不一致（被外部修改，防覆盖用户改动，报出文件清单）；
     c. 目录非空且无 manifest（来源不明，拒覆盖）。
   - forced：`--force` 覆盖并刷新 manifest。

## 六、模板文件约定（executor/auditor 填写，scaffold 只初始化）

- `REVIEW.md`：`status: pending|working|needs_review|blocked|done`、`executor_round`、`auditor_round`、
  `root: self|upstream|contract`（失败分类器）、`confidence: 0-1`、待办/已做/交接三件套小节。
- `contract.yaml`：`input.from/describe`、`output.artifacts/describe` 为占位由 executor 填；
  `read_api` 预填总任务书 `interfaces`（与总任务书语义一致）。
- `交付说明.md`：改动内容/测试结果/外部验收自测/已知风险/交接备注 五节。

## 七、退出码（CLI，机器可解析）

| 码 | 语义 | 说明 |
|---|---|---|
| 0 | created/idempotent | 生成成功（含 conflict 输入：生成 + 上抛冲突提示） |
| 1 | task_invalid | fw-protocol errors 非空（结构/环/接口重复/预算矛盾） |
| 2 | version_mismatch | expected 版本防护拒绝（a/b/c 三种） |
| 3 | io_error | 文件读取失败 / fw-protocol 不可用 / 其他异常 |
| 4 | usage | CLI 用法错误（argparse 已覆盖为 4） |

## 八、已知限制（诚实标注）

1. **契约文件路径**：`integration.contract_file` 若配置为绝对路径，scaffold 回退为
   `contracts/api.yaml`；不支持越出任务根的自定义绝对位置（目录规范 v2 规定契约区在任务根内）。
2. **派生书单书校验**：子集任务书用 fw-protocol 直接校验会报 `dep_unknown_module`（见第四节），
   属预期；由比对测试保证语义一致性，非校验通过。
3. **验收冲突**：conflict 不阻塞结构生成；冲突项由上层（runner/任务管理器）回人定优先级，
   scaffold 不代定（三权分立）。
4. **快照.json 只是初始化**：scaffold 不实现 checkpoint/resume 逻辑（归 fw-runner），只写初始
   `status=scaffolded / modules=pending` 状态；后续更新是 runner 职责。
5. **模板内容不代填**：REVIEW.md/contract.yaml/交付说明.md 只初始化模板与预填可派生部分
   （read_api/upstream-downstream），executor/auditor 填写执行期内容。
6. **manifest 是 scaffold 自身守卫**：记录的是"本次生成时"的文件快照；执行期文件（executor 产物、
   日志）不在 manifest 中，scaffold 不监控执行期写入（那属于 runner/auditor 职责）。
7. **文件名安全化**：任务/模块名中的 `/ \ : * ? " < > |` 与空白转为 `-`（`sanitize_name`），
   同名不同任务仍可能产生目录名碰撞（依赖指纹守卫拦截，换目录或 --force）。
