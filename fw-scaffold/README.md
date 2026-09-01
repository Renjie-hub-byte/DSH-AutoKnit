# fw-scaffold —— 目录脚手架（需求 2）

> dsh 任务编排层 v1.0 的第 2 个模块。读**合法 task.yaml**（经 fw-protocol `validate_file` 判定）
> 一键生成**目录规范 v2** 的任务目录树：模块文件夹 + 派生模块任务书（原子合同）+
> 模板文件（REVIEW.md / contract.yaml / 交付说明.md）+ shared/ 只读共享 + tmp/logs 豁免区 +
> 顶层文件全部 **fs 原子写 + expected 版本防护**。
> 纯 Python 3.11 脚本，复用 fw-protocol（兄弟包直接 import，免 pip install），不依赖 dsh 核心、不造轮子。

## 快速开始

```bash
cd ~/projects-hold/projects/dsh-workflow/framework-v1/fw-scaffold
python3.11 -m fw_scaffold.cli examples/task-valid.yaml --output /tmp/scaffold-demo   # exit 0
./bin/fw-scaffold examples/task-valid.yaml --output /tmp/scaffold-demo              # 同义可执行入口
```

环境要求：Python 3.11 + PyYAML + jsonschema（与 fw-protocol 相同，本机已有）。

## 生成的目录树（目录规范 v2）

```
任务-<名>_<日期>/                  ← 日期取 task.created 的 YYYY-MM-DD，缺省用当天
├── task.yaml                    ← effective 版本（fw-protocol 默认值补全后的总任务书）
├── contracts/api.yaml           ← 契约区：所有模块接口汇总（fw-integrate 基线）
├── skeleton.md                  ← 骨架说明（按 layer 派生，横向对齐防跑偏）
├── 认知/README.md               ← 规划认知区（planner 调研/滚动规划纪要）
├── shared/                      ← 只读共享区（README + .readonly 机器标记；不属于豁免区）
├── 总日志/
│   ├── dispatch.jsonl           ← 调度日志（scaffold 初始化；runner 追加）
│   ├── integration.jsonl        ← 集成验收日志（scaffold 初始化；integrate 追加）
│   └── 快照.json                ← checkpoint 初始状态（执行期更新归 fw-runner）
├── .scaffold-manifest.json      ← 版本守卫：task 指纹 + 全部生成文件 sha256
├── .scaffold-version            ← fw-scaffold/<版本> + 目录规范v2
└── modules/
    └── mXX-<名>/
        ├── src/  test/          ← 产物与测试（.gitkeep 占位）
        ├── logs/  tmp/          ← 豁免区（.auditor-ignore：auditor 忽略）
        ├── REVIEW.md            ← 验收闭环模板（机器可解析键值行）
        ├── contract.yaml        ← 接口契约模板（read_api 预填自总任务书；input/output 占位）
        ├── 任务书-mXX.yaml      ← 派生模块任务书（原子合同，字段与总任务书逐字段一致）
        └── 交付说明.md          ← 交付报告模板
```

## CLI 与退出码（机器可解析）

| 退出码 | 含义 | 触发 |
|---|---|---|
| 0 | created / idempotent | 目录树已生成；或未改动的幂等重跑；输入含验收冲突时也生成（输出中提示） |
| 1 | task_invalid | 输入 task.yaml 未通过 fw-protocol 校验（结构错误/依赖环/接口重复/预算矛盾） |
| 2 | version_mismatch | expected 版本防护：目录已存在且任务书指纹/生成文件被改动；或非空目录无清单 |
| 3 | io_error | 输入文件读取失败 / fw-protocol 不可用 / 其他异常 |
| 4 | usage | CLI 用法错误 |

`--json` 输出 `{ok, status, root, task_name, guard_status, files, directories, warnings, conflicts}`（conflicts 来自 fw-protocol 验收冲突标记，用于上层回人定优先级）。

## 与需求 2 验收标准对照

| 验收 | 实现 | 证据 |
|---|---|---|
| 1. 合法 task.yaml → 目录树生成、无缺失 | `fw_scaffold/scaffold.py::generate` + `derive.py` | `tests/test_acceptance1_tree.py`（8 用例） |
| 2. 每模块含全部子目录 + 派生任务书 + 模板 | `derive.derive_module_book` + `templates.py` | `tests/test_acceptance2_module.py`（8 用例，含逐字段一致性比对） |
| 3. shared/ 与 tmp/ 区分正确（只读共享 vs 豁免区） | shared: `README.md`+`.readonly`；logs/tmp: `.auditor-ignore` | `tests/test_acceptance3_shared_tmp.py`（7 用例） |

运行测试：`python3.11 -m pytest tests/ -q -p no:cacheprovider` → **40 passed**。

## expected 版本防护（fs 原子写）

- 每个文件写入 = 同目录临时文件 + flush/fsync + `os.replace`（等价 dsh fs 原子写；不用 Redis/外部锁）。
- 生成完成后写 `.scaffold-manifest.json`：`task_fingerprint`（将写入的 task.yaml 的 sha256）+ 全部生成文件 sha256。
- 再次生成先比对：task 指纹变 / 任一生成文件被外部修改 / 目录非空无清单 → 抛 `ExpectedVersionMismatch`（exit 2），
  需 `--force` 覆盖或换 `--output`；完全一致 → 幂等重跑（idempotent，重写相同字节）。
- 证据：`tests/test_atomic_guard.py`（10 用例）。

## 派生模块任务书（原子合同）

`任务书-mXX.yaml` = 深拷贝 effective 总任务书，`modules` 只留本模块；`task/budget/runtime/integration`
与本模块 `dependencies/interfaces/acceptance/boundaries` **逐字段保留**（语义一致、字段齐全、不扩展内容）。
YAML 注释头写明 upstream（输入来源）与 downstream（依赖本模块的模块）上下文（派生自总任务书依赖边，非新内容）。
**已知限制**：派生书是子集任务书，若其 dependencies 引用外部模块，用 fw-protocol 直接校验会报
`dep_unknown_module`——属预期（语义一致性优先），语义一致性由测试逐字段比对保证（见 `test_derived_book_fields_complete_and_consistent`）。

## 三权分立边界

scaffold 只**生成结构与模板**，不代任何角色定夺：验收冲突（快 vs 安全）由 fw-protocol 标记为
conflict → scaffold 照常生成目录但把冲突随 JSON/输出上抛（目录结构与优先级无关）；模板文件
（REVIEW.md/contract.yaml/交付说明.md）只初始化，内容由 executor/auditor 填写。

## 目录结构

```
fw-scaffold/
├── bin/fw-scaffold            # 可执行入口（免 pip install）
├── fw_scaffold/               # Python 包
│   ├── scaffold.py            # 主逻辑：校验 → 计划 → guard → 原子写 → manifest
│   ├── derive.py              # 目录名/日期/模块级任务书派生
│   ├── templates.py           # REVIEW/contract/交付说明/shared/豁免区模板
│   ├── io_utils.py            # fs 原子写 + expected 版本防护（manifest 指纹守卫）
│   └── cli.py                 # CLI（退出码 0/1/2/3/4）
├── examples/task-valid.yaml   # 合法示例（3 模块，依赖链 m01→m02→m03）
├── tests/                     # 40 用例（6 文件）
├── docs/scaffold-spec.md      # 目录规范 v2 全文 + 字段语义 + 已知限制
└── pyproject.toml             # 可选 pip install
```

## 下游复用（给 runner / integrate / auditor）

- `generate(task_yaml, output_dir, force, dry_run)` → `ScaffoldResult`（root/files/guard_status/conflicts）。
- 根 `task.yaml` = effective 版本（默认值补全），runner 拓扑调度、integrate 契约校验以此为准。
- 模块 `REVIEW.md` 的键值行（status/executor_round/auditor_round/root/confidence）供 runner 升级链读取。
- `contracts/api.yaml` = 接口汇总，`modules/*/contract.yaml`（input/output/read_api）= 运行时契约校验输入。
- `总日志/快照.json` = checkpoint 初始状态；`总日志/dispatch.jsonl`、`integration.jsonl` = 调度/集成日志。
- 已知限制详见 `docs/scaffold-spec.md` 末尾；不做超出需求的 runner/integrate/budget/preset 实现。
