# fw-presets —— 三角色 dsh preset（需求 3）

> dsh 之上任务编排层 framework-v1 的**角色预设层**：把 v0.4 设计（三权分立铁律 / REVIEW.md 模块闭环
> / 失败根因分流）落成三个可挂载的 dsh agent preset——fw-planner（规划）/ fw-executor（执行）/
> fw-auditor（验收），并为 auditor 的判定定义了**机器可解析四段**（判定/blocker/root/confidence）。
> 本目录只产出 preset 文件 + 协议 + 测试/示例；「三 preset 在 dsh GUI 可选」属收尾阶段 GUI 验收点
> （真实 GUI 挂载确认由 GUI executor 完成，本模块按说明性配置标注清楚，见下）。

## 三个 preset

| preset | 角色 | 协议绑定 | sandbox | cwd |
| --- | --- | --- | --- | --- |
| `fw-planner` | 规划者 | prd-split（四条铁律）+ 只拆不写 + 开局限预算调研 + fw-protocol schema 产出 | read-only（默认）+ approval ask | 任务总文件夹（只写规划产物） |
| `fw-executor` | 执行者 | 执行纪律：开工先读 REVIEW.md → 列 todo → 干活 → 自测外部验收 | workspace-write（物理锁死） | 模块文件夹 modules/mXX-*/ |
| `fw-auditor` | 验收者 | 验收协议：过程审计三步 + 结果对照 + 根因分类 + confidence 0-1 | read-only（可跨模块读不写） | 模块文件夹（只读） |

## 三权分立铁律（写进三份 persona）

- **规划权归 planner**：模块验收标准（acceptance）由 planner 在任务书中外部给定；
- **执行权归 executor**：executor 列自己的执行计划（todo），**永远不自定验收标准**——验收清单是
  外部给定（总 planner / 模块 planner），executor 不能新增/删改/放宽（既当运动员又当裁判 = 框架禁止）；
- **验收权归 auditor**：auditor 是唯一裁判，对照外部验收清单判定 complete/block；executor 无权
  宣告自己验收通过，auditor 只判不写（机器状态键由 runner 统一写回 REVIEW.md）。
- 失败根因分流（v0.4）：auditor block 时根因分类 `self | upstream | contract`——
  self 回同 executor 修/换人；upstream/contract 直接抛人不重试（与 fw-runner 升级链一致）。

## 目录结构

```
presets/
├── README.md                         # 本文件：挂载方式 + 三权分立语义 + 快速验证
├── fw-planner/  preset.yml + agent.cordis.yml   # v0.4 协议绑定版 persona
├── fw-executor/ preset.yml + agent.cordis.yml
├── fw-auditor/  preset.yml + agent.cordis.yml
├── protocol/
│   ├── auditor-outcome.schema.json   # auditor 判定 JSON Schema（四段 required，与 fw-runner 对齐）
│   └── four-segment-line.md          # AUDIT_RESULT 四段行规范（日志/聊天记录可 grep）
├── examples/                         # 5 组示例：pass / block-self / block-upstream /
│                                     #   block-contract / low-confidence（JSON + 报告 MD 各一）
├── fw_presets/                       # 机器契约辅助包（校验/解析，只读）
├── tests/                            # 验收2（persona 铁律）+ 验收3（四段可解析）+ 元数据
└── docs/presets-spec.md              # 完整规格：挂载步骤/三权分立语义/对齐表/已知限制
```

## 挂载方式（dsh GUI；真实 GUI 确认留收尾阶段）

dsh 从 `~/.dsh/.agent-presets/<name>/` 发现 preset（`preset.yml` 元信息 + `agent.cordis.yml` 工具
清单）；`settings.yaml` 的 `agent-presets.default` 指定默认。挂载步骤：

```bash
# 1) 把 v0.4 版 preset 装入 dsh 用户 preset 目录（覆盖 v0.2 雏形）
for p in fw-planner fw-executor fw-auditor; do
  mkdir -p ~/.dsh/.agent-presets/$p
  cp framework-v1/presets/$p/preset.yml framework-v1/presets/$p/agent.cordis.yml ~/.dsh/.agent-presets/$p/
done
# 2) 重启 dsh → GUI 新建会话 → agent 选择器应出现「规划者 Planner / 执行者 Executor / 验收者 Auditor」
#    （order 50/51/52 保持 GUI 排序稳定）
```

> 注意：真实 GUI 选择确认（三 preset 在 dsh GUI 可选、挂载验证通过）属收尾阶段 GUI 验收点，
> 本轮不做真实 GUI 操作与截图；sandbox 模式（executor=workspace-write / auditor=read-only）在
> dsh 中由会话创建时设置（不属 preset 文件强制），persona 里已把期望模式写清，真实会话创建时
> 由 runner/人工按本表设置。

## 快速验证（auditor 可独立复现，全程 <2s）

```bash
cd ~/projects-hold/projects/dsh-workflow/framework-v1/presets
PYTHONDONTWRITEBYTECODE=1 python3.11 -m pytest tests/ -p no:cacheprovider -q
```

## 与已审计框架产物对齐（不造轮子）

| 框架产物（已审计） | preset 对齐点 |
| --- | --- |
| fw-protocol（round_001） | planner 产出 task.yaml 必须过 fw-protocol 校验（退出码 0）；schema 引用 `fw-protocol/schema/task-schema.json` |
| fw-scaffold（round_002） | executor/auditor 遵循 REVIEW.md 机器键值行（status/executor_round/auditor_round/root/confidence）与 contract.yaml 结构；executor 只写内容小节 |
| fw-runner（round_004） | auditor 判定写入 `tmp/auditor-outcome.json`（字段与 DriverOutcome 一致），runner 统一写回机器状态键；block 根因 self/upstream/contract 与升级链路由一致 |
| fw-budget / fw-integrate（round_005/007） | auditor 判定数据可进回人报告/完成报告；集成验收判定流程与 auditor 结果对照同源 |

## 测试

- `tests/test_persona_rules.py` —— 验收2：三份 persona 含三权分立 + 各角色协议铁律（planner 只拆不
  写/prd-split、executor 开工先读 REVIEW.md→列 todo→自测外部验收、auditor 三步+结果对照+根因+
  confidence）
- `tests/test_auditor_outcome.py` —— 验收3：5 组示例 JSON 四段齐全可校验、报告 MD 可提取四段行且
  与 JSON 一致、round-trip、非法判定被拒、与 fw-runner DriverOutcome 字段对齐
- `tests/test_presets_metadata.py` —— 产物完整性：三 preset 文件齐全、preset.yml 元数据、文档存在

## 已知限制（诚实标注，详见 docs/presets-spec.md §限制）

1. 三 preset 在 dsh GUI 可选未做真实 GUI 验证（本轮 CLI 形态，GUI 确认留收尾）；
2. sandbox 模式由会话创建时设置，preset 文件不强制（persona 已写清期望值）；
3. `!!js` 标签为 dsh js-yaml 求值语法，python 侧 tolertant 解析保留原样不执行；
4. persona 是提示词约束，不是硬沙箱——隔离靠 dsh sandbox/confine 程序保证。
