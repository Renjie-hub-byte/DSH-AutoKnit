# AutoKnit 快速开始（从零到跑通）

> AutoKnit 是 dsh（DeepSeek Harness）之上的分治执行框架：planner 拆任务 → executor 并行写代码 → auditor 验收 → split 递归。你只审规划、合代码。

## 0. 前置概念（30 秒）

- **PRD**：你的需求文档（让 AI 跟你聊需求后产出，或自己写）。AutoKnit 按它拆任务。
- **任务目录**：`fw-scaffold` 生成的 `任务-<名>_<日期>/`，内含 `task.yaml` + `modules/`。
- **run**：一次完整执行。可 `--resume` 从 checkpoint 续跑。

## 1. 依赖清单

| 依赖 | 版本 | 说明 |
|---|---|---|
| Python | 3.11+ | 含 pyyaml / jsonschema / pytest（fw-env 自动装） |
| dsh | 最新 | DeepSeek Harness，负责会话与模型调用（见附录 A） |
| zstd | 系统 | 会话压缩（macOS 自带；Linux `apt install zstd`） |
| codegraph / semgrep | 可选 | auditor 程序采证增强，缺了自动降级 |

## 2. 安装 AutoKnit

```bash
git clone https://github.com/Renjie-hub-byte/autoknit.git
cd autoknit/framework-v1 && bash install.sh   # rsync + venv + editable 安装，装好 autoknit 命令
```

安装后验证：

```bash
autoknit doctor        # 环境体检：缺什么、怎么装，人话报错
```

`doctor` 检查 6 项：Python、dsh 二进制、凭据、模型路由、数据桥（:8765）、防睡眠。全部 ✅ 就可以开跑。

## 3. 准备 PRD

PRD 是 AutoKnit 的唯一输入。**写清楚"要什么 + 验收标准"**：

```markdown
# 订单管道

## 背景
用户下单后创建订单、扣库存、发通知。

## 功能需求
1. 创建订单（校验商品/库存/价格）
2. 扣减库存（原子操作，超卖防护）
3. 发通知（邮件/短信，失败重试 3 次）

## 验收标准
1. 创建订单后库存正确扣减（单测断言）
2. 库存不足时创建失败且不扣减（单测断言）
3. ...
```

> 提示：验收标准是 auditor 逐条核对的依据，写得越可测越好。PRD 里显式声明的模块划分，planner 会尊重。

## 4. 开跑

```bash
# ① 审规划（只规划不执行，不花执行 token）
autoknit plan-only <任务目录>          # 产出 task.yaml：几个模块、各多少行、契约清单

# ② 同意规划后执行
autoknit run <任务目录>                # 拆/派/写/验/续做/递归，全自动

# ③ 看结果
autoknit status <任务目录> --once      # 模块状态 + 验收结果
autoknit token <任务目录>              # token 账（输入/输出/缓存命中）
autoknit dashboard                     # 数据桥面板（可选）
```

## 5. 常用参数

| 参数 | 意义 |
|---|---|
| `--max-parallel N` | 并行模块数（默认 2；机器好可调大） |
| `--executor-model M` | 执行模型可换（实测 flash 档即可高质量交付） |
| `--resume` | 从 checkpoint 续跑（崩溃/中断后不重复规划） |
| `--config dflow.yaml` | 项目级配置（阈值/并行/各角色模型） |

## 6. 环境坑（踩过并已修）

| 坑 | 现状 |
|---|---|
| venv 状态漂移 / 解释器死链 → 启动静默崩 | ✅ bootstrap 试运行校验 + 预检人话报错 |
| 长任务被 macOS 合盖睡眠冻结 | ✅ fw-run 自动 `caffeinate -i` 包裹 |
| 反引号 heredoc 污染 prompt 模板 | ✅ 转义保留字面量（985ea338） |
| sandbox-exec 在 macOS 已坏（fail closed） | ✅ 默认 `danger-full-access` + 路径级越界检测兜底 |

---

## 附录 A —— 安装 dsh（DeepSeek Harness）

```bash
# ① 安装 CLI（npm 全局）
npm install -g @deepseek-ai/dsh

# ② 登录（拿到凭据）
dsh login                      # 按提示完成认证

# ③ 配置默认模型（可选，不配走 dsh 默认）
# ~/.fw-dsh/settings.yaml
agent-default-model:
  provider: deepseek-official
  model: deepseek-v4-flash
  reasoningEffort: low
```

> AutoKnit 用独立的 `~/.fw-dsh` 会话根，不碰主 `~/.dsh`（你的其他 agent 不受影响）。可用 `FW_DSH_HOME` 覆盖。

## 附录 B —— 数据桥（dashboard 数据源）

`autoknit run` 会自动拉起 `fw-api serve`（:8765，LaunchAgent `com.autoknit.fwapi-bridge` 保活）：

```bash
# 手动启停
launchctl kickstart -k gui/$(id -u)/com.autoknit.fwapi-bridge   # 重启
launchctl bootout gui/$(id -u)/com.autoknit.fwapi-bridge        # 停止

# 数据桥接口（只读）
GET /api/runs                    # run 注册表
GET /api/runs/{id}/usage         # 单 run token 明细（输入/输出/缓存）
GET /api/runs/{id}/tree          # 模块进度链
```

> 数据桥同时扫描 `~/.fw-dsh` 与 `~/.fw-dsh-bench` 两个历史会话根（bench 隔离环境遗留），run 级 token 按任务目录 + 时间窗精确聚合，不跨 run 串数据。
