# fw-integrate —— 集成验收模块（需求 6）

「dsh 之上任务编排层 framework-v1」的集成验收层：模块合流后，程序化读取契约与产物做
**运行时契约校验**（不只人工比对预测基线），按 **end_gate** 决定是否上抛回人，全部通过时
产出**完成报告**并**归档**（复用 fw-budget 归档机制）。

## 解决什么

| 问题 | fw-integrate 的做法 |
| --- | --- |
| 集成靠人工比对预测基线 | 程序检查四项：接口匹配 / 数据格式 / 跨模块数据依赖 / 预测基线 will_have·will_not_have 对照，输出**匹配/缺失清单**（验收2） |
| 两模块接口对不上才发现 | 契约区 `contracts/api.yaml` vs 各模块 `contract.yaml` read_api 运行时对照，错误**点名具体哪两个模块**（验收1） |
| B 用了 A 没声明的输出 | 跨模块数据依赖检查：B 的 `input.from` 需要 A 的输入时，A 的 `output.artifacts` 必须声明过且产物真实存在 |
| 通过后没有收口动作 | 全部通过 → 完成报告（markdown）+ 归档（fw-budget archive 机制 + cause 修正为 completed）（验收3） |

## 架构

```
task.yaml(effective, fw-protocol 复校验)
        │
        ▼
┌──────────────────────────── fw-integrate ────────────────────────────┐
│  IntegrateContext（只读）                                               │
│   ├─ contracts/api.yaml ─────────── 契约区（接口基线）                  │
│   ├─ modules/*/contract.yaml ────── 运行时契约（input/output/read_api） │
│   ├─ modules/*/REVIEW.md ────────── 验收闭环（status 键）               │
│   └─ 总日志/快照.json ───────────── run_id / 模块状态                   │
│                                                                       │
│  run_checks() ── 程序检查三项 + 基线对照                                  │
│   ├─ check_interfaces     接口匹配（点名哪两个模块）                     │
│   ├─ check_data_format    产物存在 + JSON/YAML/CSV 解析级校验            │
│   ├─ check_data_dependency B 需要 A 的输入 → A 的 output 声明过？        │
│   └─ check_baseline       will_have 匹配/缺失 + will_not_have 违反/clean │
│                                                                       │
│  end_gate 分流（auto=异常才找人 / always=人工确认）                      │
│        │ auto & 通过                                                    │
│        ▼                                                                │
│  完成报告.md + 归档（复用 fw-budget.manage.archive）                    │
└───────────────────────────────────────────────────────────────────────┘
        ▲ integration_hook=FwIntegrateHook()（挂在 fw-runner 上：只判不归档）
```

## 快速开始

```bash
# 1) 脚手架生成任务目录（fw-scaffold）
python3.11 -m fw_scaffold.cli task.yaml --output ./

# 2) 跑完所有模块（fw-runner，可注入本模块钩子）
python3.11 -m fw_runner.cli run <任务根>

# 3) 集成验收（只检查）
./bin/fw-integrate check <任务根> --json

# 4) 全部通过 → 完成报告 + 归档
./bin/fw-integrate complete <任务根> --reason "集成验收通过" --json

# 4b) end_gate=always：fw-runner 会停在 needs_confirmation（快照非 complete），
#     人工确认后用 confirm 收口（完成报告 + 归档，exit 0）
./bin/fw-integrate confirm <任务根> --reason "人工确认通过" --json

# 5) 或一键全流程（runner 注入钩子 + 通过后归档）
./bin/fw-integrate run <任务根> --json
```

### 程序化（Python API）

```python
from fw_integrate import run_checks, complete_and_archive, FwIntegrateHook
from fw_integrate.context import load_integrate_context

ic = load_integrate_context("<任务根>")
report = run_checks(ic)                 # IntegrationCheckReport
print(report.errors, report.baseline.matched, report.baseline.missing)
res = complete_and_archive("<任务根>")  # 通过→完成报告+归档；失败→IntegrateFailed
res = confirm_and_archive("<任务根>")   # end_gate=always 人工确认→完成报告+归档
```

### 挂到 fw-runner（钩子形态）

```python
from fw_runner.runner import run as runner_run
from fw_integrate.hook import FwIntegrateHook

result = runner_run(root, executor_driver=..., auditor_driver=...,
                    integration_hook=FwIntegrateHook())
# 通过→complete；接口不匹配/基线缺失→integration_failed（exit 2 抛人）
```

## CLI 与退出码（机器可解析）

```
fw-integrate check TASK_ROOT [--json]
fw-integrate complete TASK_ROOT [--reason TEXT] [--json]
fw-integrate confirm TASK_ROOT [--reason TEXT] [--json]   # end_gate=always 人工确认归档
fw-integrate run TASK_ROOT [--executor-cmd CMD] [--auditor-cmd CMD] [--mode MODE] [--json]
```

| 退出码 | 含义 | 场景 |
| --- | --- | --- |
| 0 | ok | check 通过 / complete 完成归档成功 / confirm 人工确认归档成功 |
| 1 | input_error | 任务根无 task.yaml、任务书复校验失败、契约区缺失、快照状态不符合收口前置 |
| 2 | needs_human | 集成失败（接口不匹配/产物缺失/基线缺失违反）或 end_gate=always 待人工确认（runner 先停） |
| 3 | io_error | 运行期异常 |
| 4 | usage | 参数错误 |

## 配置项（读 effective 任务书）

| 配置 | 来源 | 语义 |
| --- | --- | --- |
| `integration.contract_file` | task.yaml | 契约区路径（默认 `contracts/api.yaml`） |
| `integration.check.interface_duplicate` | task.yaml | false 跳过接口匹配检查 |
| `integration.check.cross_module_data_dependency` | task.yaml | false 跳过跨模块数据依赖检查 |
| `integration.check.prediction_baseline` | task.yaml | false 跳过预测基线对照（数据格式为集成固有职责，无开关） |
| `runtime.end_gate` | task.yaml | `auto`=异常才找人；`always`=全通过也需人工确认（不自动归档） |
| `task.prediction_baseline.will_have/will_not_have` | task.yaml | 预测基线清单（对照输入） |

## 验收对照（需求 6）

| 验收 | 证据 |
| --- | --- |
| 1) 接口不匹配→报错指出哪两个模块 | tests/test_acceptance1_interface_mismatch.py；`fw-integrate check` exit 2，errors 同时含 m01 与 m02 |
| 2) 预测基线对照→匹配/缺失清单 | tests/test_acceptance2_baseline.py；报告 `baseline.matched / baseline.missing`（带证据路径） |
| 3) 全部通过→完成报告+归档 | tests/test_acceptance3_complete_archive.py；`fw-integrate complete` exit 0，archived/ + 完成报告.md + ARCHIVE.md + 快照 cause=completed |

## 依赖复用（不造轮子）

| 能力 | 复用来源 | 说明 |
| --- | --- | --- |
| 任务书 schema + 校验 | fw-protocol（round_001 已审计） | `validate_file().effective` 是全部语义输入 |
| 契约/产物结构 | fw-scaffold（round_002 已审计） | contracts/api.yaml、contract.yaml、交付说明.md |
| 钩子/事件/快照 | fw-runner（round_004 已审计） | IntegrationHook、integration.check 事件、快照 schema v3 |
| 归档机制 | fw-budget（round_005 已审计） | `fw_budget.manage.archive` 直接调用；归档树内回写 cause=completed |
| fs 原子写 | 同 dsh 语义 | 临时文件 + os.replace，无外部锁 |

真实 dsh 能力（session-query 查询各模块契约快照/产物清单）为适配点：`IntegrateContext` 的
加载器可替换，当前以文件系统读取为本地等价物（诚实标注，见 docs/integrate-spec.md §8 已知限制）。

## 目录结构

```
fw-integrate/
├── bin/fw-integrate           可执行 CLI
├── docs/integrate-spec.md     规范：检查语义/证据面口径/退出码/已知限制
├── examples/
│   ├── executor-conform.py    示例：按契约交付的 executor 驱动（input.from/output.artifacts/基线证据）
│   └── auditor-conform.py     示例：契约一致验收的 auditor 驱动（四段式 pass/block）
├── fw_integrate/
│   ├── context.py             契约源加载（只读）
│   ├── checks.py              接口匹配/数据格式/跨模块数据依赖
│   ├── baseline.py            预测基线对照（关键词启发式）
│   ├── report.py              集成报告 + 完成报告 + integration.jsonl 事件
│   ├── archive.py             完成归档（复用 fw-budget）
│   ├── hook.py                FwIntegrateHook（fw-runner 钩子，只判不归档）
│   └── cli.py                 CLI（check/complete/run）
└── tests/                     47 个测试（12 文件）
```
