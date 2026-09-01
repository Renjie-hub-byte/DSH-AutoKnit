# fw-api —— AutoKnit 对外接口层（dsh.* 接口）

> AutoKnit 的普适对接面：任何前端（DSH client 插件 / 自建 cockpit / MCP）通过
> `dsh.*` 接口消费 AutoKnit 任务状态与交互。可随 AutoKnit 开源打包，不依赖 DSH 前端。

## 是什么

AutoKnit 有"人在环上"（auditor 打回 / split 歧义 / 外部信息请求会停下来等人）。fw-api
把这套能力暴露成**稳定接口协议**，任何面板/前端按接口对接即可，不用读框架内部文件。

本包收敛自 dsh_cockpit 系列**已验证实现**（m01-m05），统一到 `fw_api` 命名空间。

## 接口清单

| 接口 | 入口 | 干什么 |
|---|---|---|
| `dsh.task.list` | `fw_api.dsh.task.list(task_dir)` | 任务列表（阶段/模块状态/紧急度排序）|
| `dsh.task.detail` | `fw_api.dsh.task.detail(task_dir, run_id)` | 单个 run 模块级状态 |
| `dsh.task.update` | `fw_api.dsh.task.update.push(event)` | 状态变化广播（R→F）|
| `dsh.task.reply` | `fw_api.dsh.task.reply.submit(task_id, command, ...)` | needs_human 回复（continue/retry/revise/自定义）|
| `dsh.session.detail` | `fw_api.dsh.session.detail(data_dir, session_id)` | 会话详情（token/缓存/轮数）|
| `dsh.usage.summary` | `fw_api.dsh.usage.summary(source)` | 消耗汇总 |
| `dsh.usage.update` | `fw_api.dsh.usage.update(...)` | 消耗变化载荷 |

数据桥（只读）：`fw_api.fwr_dir.read(task_dir)`（任务目录解析）、
`fw_api.fwr_status.compute(raw)`（阶段/模块状态计算，含 switch/needs_human）。

## 用法

```python
import fw_api

# 任务列表（紧急度排序：needs_human > switch > auditor > executor > ...）
result = fw_api.dsh.task.list("/path/to/task_dir")
for task in result["tasks"]:
    print(task["run_id"], task["stage"], "needs_human" if task["needs_human"] else "")

# 单 run 详情
detail = fw_api.dsh.task.detail(task_dir, run_id)

# needs_human 任务回复（continue / retry / revise / 自定义）
fw_api.dsh.task.reply.submit(task_id=run_id, command="continue",
                             instruction="继续，按新方案来", run_dir=task_dir)
```

## 设计原则（继承自历史验证实现）

- **只读数据桥**：不写任务状态文件；确定性空降级、永不抛异常。
- **确定性输出**：同输入多次调用结果精确相等（紧急度/排序/键序固定）。
- **兼容命名空间**：`dsh_task_list` / `fwr_detail` / `fwr_dir` / `fwr_status` 保留为
  兼容子模块，历史调用方不改 import 即可迁移。
- **目录结构适配 v1.0**：快照读 `总日志/快照.json`，兼容历史 `快照.json`/`snapshot.json`。

## 测试

```bash
cd fw-api && PYTHONPATH=src python3.11 -m pytest test/ -q   # 53 passed
```
