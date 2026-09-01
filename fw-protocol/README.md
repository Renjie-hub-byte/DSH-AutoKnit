# fw-protocol —— 任务书协议与校验器（需求 1）

> dsh 任务编排层 v1.0 的第 1 个模块。定义 `task.yaml` 的 JSON Schema，并实现三查校验器：
> **依赖环检测（DFS）** / **接口重复检测（前缀+方法）** / **验收冲突检测（快 vs 安全 → 回人定优先级）**。
> 全部为纯 Python 脚本 + JSON Schema，不依赖 dsh 核心、不依赖外部服务。

## 快速开始

```bash
cd ~/projects-hold/projects/dsh-workflow/framework-v1/fw-protocol
./bin/fw-protocol examples/task-valid.yaml          # 期望 exit 0（PASS）
./bin/fw-protocol examples/task-cycle.yaml          # 期望 exit 1（依赖环，指出环路径）
./bin/fw-protocol examples/task-interface-dup.yaml  # 期望 exit 1（接口重复）
./bin/fw-protocol examples/task-conflict.yaml       # 期望 exit 2（验收冲突 → 人工定优先级）
```

环境要求：Python 3.11（`python3.11`，本机已有）+ PyYAML + jsonschema。

## 目录结构

```
fw-protocol/
├── schema/task-schema.json   # JSON Schema（draft 2020-12），机器定义
├── fw_protocol/              # Python 包
│   ├── model.py              # Issue / ValidationResult（结构化结果）
│   ├── schema.py             # schema 加载 + 默认值套用（effective 任务书）
│   ├── validate.py           # 主入口：结构校验 + 三查 + 预算自检
│   ├── dependencies.py       # 依赖环 DFS（含 id 唯一/未知依赖/重复依赖）
│   ├── interfaces.py         # 接口重复检测（前缀+方法交集）
│   ├── conflicts.py          # 验收冲突关键词检测（只标记、不代定优先级）
│   ├── io_utils.py           # YAML 读取封装
│   └── cli.py                # CLI 入口（退出码 0/1/2/3/4）
├── bin/fw-protocol           # 可执行入口（免 pip install）
├── examples/                 # 4 个示例任务书（合法/环/接口重复/冲突）
├── tests/                    # pytest 测试（33 个用例，覆盖需求1 验收 1/2/3）
├── docs/schema.md            # 字段含义 + 默认值 + 三查语义 + 退出码 + 已知限制
└── pyproject.toml            # 可选 pip install（scripts: fw-protocol）
```

## 运行测试

```bash
cd ~/projects-hold/projects/dsh-workflow/framework-v1/fw-protocol
python3.11 -m pytest tests/ -v        # 33 passed
```

## 与需求 1 验收标准对照

| 验收 | 实现位置 | 证据 |
|---|---|---|
| 1. 含环依赖图 → 报错指出环路径 | `fw_protocol/dependencies.py`（DFS） | `examples/task-cycle.yaml` → exit 1，输出 `依赖环: m01 → m03 → m02 → m01`；`tests/test_dependency_cycle.py` |
| 2. 两模块同接口前缀+方法 → 报错 | `fw_protocol/interfaces.py` | `examples/task-interface-dup.yaml` → exit 1，指出 m01/m02 与共享方法；`tests/test_interface_duplicate.py` |
| 3. 合法任务书 → 通过无告警 | `fw_protocol/validate.py` | `examples/task-valid.yaml` → exit 0，errors/conflicts/warnings 全空；`tests/test_valid_task.py::test_valid_task_passes_clean` |
| 4. schema 文档齐全（字段含义+示例） | `schema/task-schema.json` + `docs/schema.md` | 字段表 + 默认值 + `examples/task-valid.yaml` 完整示例 |

## 三权分立边界

本模块只做**校验与标记**，不代任何角色定夺：
- 验收冲突 → 标记 `conflict`（退出码 2）回人定优先级，**不在代码里排优先级**。
- 三查只报告问题与结构化依据（`detail`），不自动修改任务书。

## 已知限制（详见 docs/schema.md 第八节）

1. 通配符接口不做"语义覆盖"重叠检测（只精确前缀重复）。
2. 验收冲突关键词为启发式，可能误报/漏报（误报方向安全）。
3. `prediction_baseline` / `cross_module_data_dependency` 开关仅 schema 承载，执行在 fw-integrate。

## 下游复用（给 scaffold / runner / integrate）

- `validate_file(path).effective` → 补默认值后的完整任务书（scaffold 派生模块任务书、runner 拓扑的输入）。
- `result.status` / 退出码 → 编排层决定进入执行（0）、回 planner（1）、回人拍板（2）。
- Python API：`from fw_protocol import validate_document, validate_file`。
