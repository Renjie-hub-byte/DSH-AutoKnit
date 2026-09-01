# presets-spec —— 需求3 三角色 dsh preset 完整规格（v0.4）

> 配套：`../README.md`（速览 + 挂载）｜ 本文件 = 挂载步骤 / 三权分立语义 / 角色协议铁律 /
> 机器可解析输出协议 / 与已审计产物对齐 / 已知限制。

## 1. 目标

把 v0.4 设计（三权分立铁律、REVIEW.md 模块闭环、失败根因分流、auditor 低置信升级）落成三个
可挂载的 dsh agent preset，并保证 auditor 判定（四段：判定/blocker/root/confidence）可被程序
解析消费（供 fw-runner 升级链、回人报告、集成验收判定使用）。

## 2. 三权分立语义（写进三份 persona 的公共约定）

| 权 | 归属 | 能做什么 | 不能做什么 |
| --- | --- | --- | --- |
| 规划权 | planner | 拆模块/依赖/接口前缀/模块级验收；开局调研（限预算） | 只拆不写：不写实现、不执行、不验收 |
| 执行权 | executor | 列执行计划（todo）、干活、对照外部验收清单自测 | **永不自定验收标准**；不跨模块；不宣告自己验收通过 |
| 验收权 | auditor | 对照外部验收清单判定 complete/block、根因分类、置信度 | 只判不写：不改实现、不新增/删改验收项 |

- 验收清单 = planner 在任务书（任务书-<id>.yaml acceptance + 预测基线）外部给定，
  executor 与 auditor 都不得改动；这就是「裁判标准外部化」。
- 机器状态键（status/executor_round/auditor_round/root/confidence/block_count/executor_id/
  executor_switches）由 fw-runner 统一写回 REVIEW.md（单一写者），executor/auditor 只写内容小节
  （待办/已做/问题与根因/交接/交付说明）与判定产物（tmp/auditor-outcome.json）——无 runner 时
  （GUI 手动模式）由 auditor 回写判定小节，格式保持键值行。

## 3. 各角色协议铁律（persona 内嵌，测试逐条断言）

### fw-planner（prd-split 协议 + 只拆不写）
1. 四条铁律：① 变更隔离拆 ② 接口只到「路径前缀+方法」 ③ 树深≤3 ④ 骨架先行；
2. 只拆不写：只产出 task.yaml / contracts/api.yaml / skeleton.md / 认知/，不写实现；
3. 开局可调研（web/codegraph/markitdown），调研 token 计入 budget.max_tokens，超预算即停；
4. 产出 task.yaml 遵循 fw-protocol schema，交付前必须 fw-protocol 校验通过（退出码 0）；
5. 自检：依赖无环 / 接口无重复 / 验收冲突关键词（快 vs 安全）→ 标记回人定优先级，不代定；
6. 预测基线：will_have / will_not_have（auditor 对照用）。

### fw-executor（执行纪律）
1. 开工先读 REVIEW.md（上一轮 auditor 反馈 root/detail/blocker 或换人交接说明）；
2. 列 todo：把本模块验收清单拆成可执行步骤（写 REVIEW.md 待办小节；是拆执行步骤，不是改验收）；
3. 干活：只写本模块（cwd=模块文件夹，sandbox workspace-write），debug-first，codegraph 查影响面；
4. 自测外部验收：交付前逐条对照任务书-<id>.yaml acceptance 自测（通过/不通过+证据），
   跑测试通过才交付；**自测 ≠ 自定验收**；
5. 根因自省：被 block 读 REVIEW root/detail——self 按反馈修；upstream/contract 不硬扛，
   上报等待（升级链直接回人）；
6. REVIEW 写入规矩：只写内容小节；机器键由 runner 写回；shared/ 只读、tmp//logs/ 豁免。

### fw-auditor（验收协议）
1. 过程审计三步：① 重放事件流（filterEvents/session-query 核「声称 vs 实际」）② 对照验收清单
   ③ 测试真伪校验（看测试内容，不是看"测试通过"）；
2. 结果对照：产物 vs contract.yaml output.artifacts（存在+可解析）、read_api vs 契约区
   （接口不匹配点名两模块）、预测基线 will_have/will_not_have；
3. 输出四段（机器可解析）：判定（verdict=pass|block）/ blocker / root（self|upstream|contract）/
   confidence（0-1）；block 时 root 必填，pass 时 root 空；
4. confidence < 0.7 → 低置信（pro 重审/仲裁，设计 9.4）；
5. 根因分流语义与 fw-runner 升级链一致：self→重试链；upstream/contract→直接回人不重试；
6. 只读：sandbox read-only，可跨模块读不写；只写 tmp/auditor-outcome.json 一个判定产物。

## 4. 机器可解析输出协议（验收3）

两层投影，字段名一致（verdict/root/confidence/blocker/reason）：

1. **canonical JSON**：`tmp/auditor-outcome.json`，JSON Schema =
   `protocol/auditor-outcome.schema.json`（required：verdict/blocker/root/confidence；
   与 fw-runner `DriverOutcome.from_mapping` 消费字段对齐）；
2. **日志四段行**：`AUDIT_RESULT|verdict=..|blocker=..|root=..|confidence=..`
   （规范见 protocol/four-segment-line.md；禁含 `|` 于 blocker；报告文本取第一行）。

测试（tests/test_auditor_outcome.py）：5 组示例（pass / block-self / block-upstream /
block-contract / low-confidence）JSON 校验 + 报告行提取 + round-trip + 非法拒绝 +
与 fw-runner 对齐（guarded 导入）。

## 5. 挂载步骤（dsh GUI；真实 GUI 确认留收尾阶段）

```bash
# 覆盖 v0.2 雏形，装入 v0.4 协议绑定版
for p in fw-planner fw-executor fw-auditor; do
  mkdir -p ~/.dsh/.agent-presets/$p
  cp framework-v1/presets/$p/preset.yml framework-v1/presets/$p/agent.cordis.yml ~/.dsh/.agent-presets/$p/
done
# 重启 dsh → 新建会话 → agent 选择器应出现三 preset（order 50/51/52）
```

GUI 确认项（收尾阶段 GUI executor 负责）：三 preset 在 dsh GUI 可选、挂载验证通过、
persona 生效。本轮 preset 内只提供说明性配置（preset.yml 元信息 + agent.cordis.yml 工具清单 +
persona 协议绑定）。

## 6. 与已审计框架产物对齐表

| 已审计产物 | 对齐点 | 形式 |
| --- | --- | --- |
| fw-protocol（round_001） | task.yaml schema / 三查 / 退出码 0/1/2 | planner persona 引用 + 产出要求 |
| fw-scaffold（round_002） | REVIEW.md 机器键行 / contract.yaml / 任务书-mXX.yaml / shared vs tmp | executor/auditor persona 规矩 |
| fw-runner（round_004） | DriverOutcome 字段 / 升级链路由 / REVIEW 单一写者 / 交接三件套 | auditor-outcome.schema.json + 四段行 + persona |
| fw-budget / fw-integrate（round_005/007） | 回人信息完备 / 完成报告判定流程 | 判定数据可被回人报告/集成判定消费 |

## 7. 已知限制（诚实标注）

1. **dsh GUI 可选性未实测**：三 preset 在 dsh GUI 可选/挂载验证属收尾阶段 GUI 验收点，本轮未做
   真实 GUI 操作与截图；
2. **sandbox 模式非 preset 强制**：executor=workspace-write / auditor=read-only 由会话创建时设置
   （dsh sandbox/mode 事件），persona 只写期望值，真实创建由 runner/人工配置；
3. **`!!js` 标签**：agent.cordis.yml 沿用 dsh js-yaml 求值语法（如 tool-bash disabled 行），
   python 侧用 TolerantLoader 保留原样不执行；
4. **persona 是提示词约束**：隔离与只读的硬保证靠 dsh sandbox/confine 程序，不靠提示词；
5. **四段行是日志投影**：canonical 机器契约是 tmp/auditor-outcome.json；四段行仅供 grep/人读，
   解析以 JSON 为准；
6. **未接入真实 dsh 会话**：本轮为文件/代码形态，dsh sessions.fork / GUI 挂载的真实集成留
   需求7/收尾；persona 协议与已审计 runner 驱动契约已对齐，接缝明确。
