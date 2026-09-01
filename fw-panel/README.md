# fw-panel —— AutoKnit 真人交互面板（后端逻辑）

> AutoKnit v2 需求3（后端部分）：给 AutoKnit 任务一个"人在环上"的看板后端——
> 读任务运行快照/事件，计算进度/消耗/待决策，管暂停/继续/回复落盘。纯 python，无 LLM。

## 是什么

AutoKnit 有人机协作接口（auditor 连续打回 / split 歧义 / 外部信息请求 / end-gate 会停
下来等人）。fw-panel 提供把"该等什么、进度如何、花了多少"算出来的后端逻辑，以及
回人决策 / 暂停 / 继续 / 提交回复的落盘与信号，供前端面板（DSH client-plugin）消费。

## 数据来源 / 消费者契约

- **读**：任务目录 `总日志/快照.json`（阶段/角色/消耗）、`总日志/dispatch.jsonl`（事件流）。
- **人决策落盘**：`总日志/human_answer.json`（既有框架 `human.py`/runner resume 读它接续）。
- **暂停信号**：框架控制（暂停到当前节点结束，交给 runner 能力）。

## 模块

```
src/autoknit_panel/
  snapshot.py    读快照 → 阶段/进度/消耗/待决策原始数据
  state.py       从事件流推导运行阶段（planning/exec/audit/split/idle）
  progress.py    进度计算
  consumption.py 消耗（输入/输出/缓存/耗时）
  pending.py / decision.py / blocker.py  待决策 + A/B/C/D/text + 阻塞语义
  answer.py      human_answer 提交落盘
  control.py     暂停/继续包装（面板控制通道）
  events.py / paths.py / enums.py / builder.py  支撑
```

## 测试

```bash
cd fw-panel && PYTHONPATH=src python3.11 -m pytest test/ test_m03a/ -q   # 88 passed
```

## 前端接入

前端侧 DSH client-plugin（`dsh-client-ui-autoknit`，挂 layout `details` 槽位右侧可折叠
细长条）见 `plugin/` 目录与其挂载说明。面板后端经 verify_plugin 验证注入机制可行；
**真正挂到线上 DSH web profile 需先把前端插件构建为完整可安装的 npm client-plugin 包**
（带 package.json 的 `dsh.client` 声明段），再登记进 web profile bundles —— 见 plugin/docs/。
