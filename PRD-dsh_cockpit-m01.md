# dsh_cockpit m01 —— DSH 任务状态数据桥（SPLIT 验证）

## 背景

dsh_cockpit 是给 ai-cockpit 做 DSH 适配的独立后端。本模块（m01）是第一个模块，历史版本因为模块太大 + executor 执行时间限制，反复被打回做不完（只产出 models.py 骨架就卡住）。

本次用升级后的 framework-v1（带 SPLIT 递归拆分）重跑，验证：模块太大时能否自动拆细完成。

## 目标

dsh_cockpit 独立后端：新增 fw-runner 数据源，读取 DSH 任务目录状态（snapshot / dispatch / 模块 tmp），产出带 DSH 阶段与模块状态的任务列表，并事件驱动广播。

## 功能需求

1. 新增 fw-runner 数据源：解析指定任务目录（task.yaml / snapshot.json / dispatch.jsonl / modules/*/tmp），产出任务列表，每条含阶段状态（planning / executor / auditor / 打回 / 换人 / needs_human）与模块状态
2. dsh.task.list 接口：按紧急度排序返回任务数组；任务目录缺失 / 无活跃 run 时确定性空降级不报错
3. dsh.task.update 广播：任务目录状态文件变化触发广播（事件驱动，非轮询）
4. dsh.task.detail 接口：返回指定 run 的模块级状态，覆盖 executor 执行中 / auditor 验收中 / 打回 N 次 / 换人中
5. 不影响既有 task.list（lh-harness 数据源）：新数据源可配置独立开关，旧接口回归测试通过

## 接口（前缀 + 方法级，不定义字段）

- `dsh.task.list`（F→R 拉取任务列表，含阶段/模块状态）
- `dsh.task.detail`（F→R 拉取单个 run 的模块级状态）
- `dsh.task.update`（R→F 任务状态变化广播，阶段变更/模块变更/needs_human 出现）

## 边界约束

- 只读 fw-runner 任务目录，不写任何任务状态文件
- 不删除/迁移旧 lh-harness 数据源（保留兼容开关）
- 纯代码 + 单测验证，不做 GUI/浏览器验证

## 验收标准（可测可验证）

1. 新增 fw-runner 数据源可解析指定任务目录产出任务列表，每条含阶段状态与模块状态（pytest 注入临时目录断言解析结果）
2. dsh.task.list 按紧急度排序返回任务数组；任务目录缺失/无活跃 run 时确定性空降级不报错
3. 任务目录状态文件变化触发 dsh.task.update 广播（事件驱动非轮询）；单测注入文件变化事件断言广播触发
4. dsh.task.detail 返回指定 run 的模块级状态，覆盖 executor 执行中/auditor 验收中/打回 N 次/换人中
5. 不影响既有 task.list（lh-harness 数据源）：新数据源可配置独立开关，旧接口回归测试通过
