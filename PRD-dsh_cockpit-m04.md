# dsh_cockpit m04 —— 建对话/建 agent 工作区绑定服务

## 背景

dsh_cockpit 是给 ai-cockpit 做 DSH 适配的独立后端。本模块（m04）是第四个模块（m01 任务状态数据桥、m02 会话与消耗数据桥、m03 请示人工回复服务之后）：实现独立的建项服务——建 agent 必须指定工作区（一个 agent 固定一个工作区），新增建对话能力（选工作区 + 模式），校验并持久化。

## 目标

dsh_cockpit 独立后端：独立建项服务——建 agent 必须指定工作区、新增 `dsh.session.create` 绑定 agent 工作区与模式，校验并持久化。

## 功能需求

1. `agent.create` 扩展 workspace 参数：建 agent 必须指定工作区（一个 agent 固定一个工作区），不传/传空 → 明确 error；传了 → 复用既有 agent.create 链路真建 agent 并返回带 workspace 的 resp
2. 新增 `session.create`：入参 agent/workspace + mode 创建 DSH 会话并绑定工作区，成功返回会话 key，失败确定性 error
3. 建 agent 时校验 workspace 目录存在且可访问，不存在 → error
4. 一个 agent 固定一个工作区：同 agent 重复建会话复用其工作区（不新建）
5. 既有 agent.list / agent.detail / agent.switch 不受影响（回归测试通过）

## 接口（前缀 + 方法级，不定义字段）

- `agent.create`（F→R 建 agent，扩展 workspace 参数——建 agent 必须指定工作区）
- `session.create`（F→R 建对话：选工作区 + 模式，绑定 agent 工作区）
- `session.create.resp`（R→F 建对话结果，会话 key）
- `session.create.error`（R→F 建对话失败：工作区不存在/绑定失败等）

## 边界约束

- 不改 DSH 会话文件内部结构，复用官方 CLI/API 建项
- 不做模型/预算管理（归既有 model.* / token.* 域）
- 纯代码 + 单测验证，不做 GUI / 浏览器验证
- **⛔ 独立实现铁律（最高优先级）**：禁止读取 / 参考任何既有实现作为"答案"——包括本项目旧任务目录（`任务-dsh_cockpit_2026-08-22` 及其 modules/）、framework-v1 内其它模块/任务目录（`任务-dsh_cockpit_m01_*`、`任务-dsh_cockpit_m02_*`）的源码。只可读本模块自己的文件 + 任务书/契约/PRD 规格。参考即失去对比独立性。

## 验收标准（可测可验证）

1. agent.create 扩展 workspace：不传/传空 → 明确 error；传了 → 真建 agent 且返回带 workspace 的 resp（复用既有 agent.create 链路）
2. 新增 session.create：入参 agent/workspace + mode 创建 DSH 会话并绑定工作区，成功返回会话 key，失败确定性 error
3. 建 agent 时校验 workspace 目录存在可访问，不存在 → error
4. 一个 agent 固定一个工作区：同 agent 重复建会话复用其工作区（单测断言）
5. 既有 agent.list / agent.detail / agent.switch 不受影响（回归测试通过）
