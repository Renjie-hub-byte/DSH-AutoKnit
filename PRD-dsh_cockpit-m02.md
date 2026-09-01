# dsh_cockpit m02 —— DSH 会话与消耗数据桥

## 背景

dsh_cockpit 是给 ai-cockpit 做 DSH 适配的独立后端。本模块（m02）是第二个模块：新增 DSH 会话数据源，读取 DSH 会话与 fw-token 数据，产出对话详情与消耗汇总，供 cockpit 面板展示 token 用量 / 缓存命中 / 速度。

## 目标

dsh_cockpit 独立后端：新增 DSH 会话数据源，读取 DSH 会话与 fw-token 数据，产出对话详情（轮数 / 步数 / token 速度 / 缓存命中 / token 量）与消耗汇总。

## 功能需求

1. 新增 DSH 会话数据源：给定 DSH 会话文件 / 目录或 fw-token 输出，解析出轮数、步数、token 速度、缓存命中、token 量
2. dsh.session.detail 返回指定会话上述字段；会话不存在 → 确定性空 / 明确错误信息，不崩
3. dsh.usage.summary 返回 DSH 会话 / 任务消耗汇总，与 fw-token 实测值对拍一致
4. 会话 / 消耗数据文件变化触发 dsh.usage.update 推送（事件驱动非轮询）
5. 解析异常（含 zstd 多帧会话损坏等）有兜底：返回确定性空 / 错误，不误报不崩

## 接口（前缀 + 方法级，不定义字段）

- `dsh.session.detail`（F→R 拉取 DSH 会话对话详情）
- `dsh.usage.summary`（F→R 拉取消耗汇总）
- `dsh.usage.update`（R→F 消耗数据变化广播）

## 边界约束

- 只读消费 DSH 会话文件，不解析修复 / 重写会话文件本体
- 不做 token 记账 / 扣费（归既有 token.usage/trend 域）
- 纯代码 + 单测验证，不做 GUI / 浏览器验证

## 验收标准（可测可验证）

1. 新增 DSH 会话数据源：给定 DSH 会话文件 / 目录或 fw-token 输出，可解析出轮数、步数、token 速度、缓存命中、token 量（用夹具 / 真实 fw-token 输出断言字段）
2. dsh.session.detail 返回指定会话上述字段；会话不存在 → 确定性空 / 明确错误信息，不崩
3. dsh.usage.summary 返回 DSH 会话 / 任务消耗汇总，与 fw-token 实测值对拍一致
4. 会话 / 消耗数据文件变化触发 dsh.usage.update 推送（事件驱动非轮询）
5. 解析异常（含 zstd 多帧会话损坏等）有兜底：返回确定性空 / 错误，不误报不崩
