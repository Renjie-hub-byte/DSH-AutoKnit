# AutoKnit 安装指南

> 开源形态：**clone 下来 → 一键安装 → 直接 `autoknit` 可用**。
> 依赖假设：你已自行安装并登录 [dsh CLI](https://www.npmjs.com/package/dsh)（本安装脚本不负责装 dsh）。

## 三段式上手

```bash
# 1. clone 仓库
git clone <仓库地址> dsh-workflow && cd dsh-workflow/framework-v1

# 2. 一键安装（默认装到 ~/.autoknit，幂等，可重复跑）
bash install.sh

# 3. 直接用
autoknit                 # 无参数打印用法帮助
autoknit new <PRD.md>    # PRD → 任务目录（planner 真身，需 dsh）
autoknit demo <任务目录>  # 链路联调（0 token，无需 dsh）
```

> 若提示 `autoknit: command not found`，把 launcher 目录加进 PATH 后重开终端：
> ```bash
> export PATH="$HOME/.local/bin:$PATH"
> ```

## 安装行为说明

| 项 | 说明 |
|---|---|
| 安装根目录 | 默认 `~/.autoknit/`，可 `AUTOKNIT_HOME` 覆盖 |
| 框架目录 | `$AUTOKNIT_HOME/framework-v1/`（launcher 里的 `FW1` 指向这里） |
| Python 环境 | 独立 venv `$AUTOKNIT_HOME/venv`（Python 3.11+） |
| launcher | `~/.local/bin/autoknit`（可用 `BIN_DIR` 覆盖） |
| 幂等 | 是。重复运行 = 增量同步 + venv 健康自检 + launcher 覆盖重写 |

安装时会 pip 安装 `pyyaml jsonschema`，并以 editable 方式安装 7 个框架子包：
`fw-protocol` `fw-scaffold` `fw-runner` `fw-api` `fw-planonly` `fw-budget` `fw-integrate`。

复制源码时排除以下内容（不影响运行）：
任务目录（`任务-*`）、实验目录（`实验-*`）、对比记录（`对比记录-*`）、根级历史 `.md` 文档、
`.git` / `.venv` / `__pycache__` / `.pytest_cache` / `.codegraph` / `.harness` / `node_modules` / `build` / `*.egg-info`。

## 配置覆盖

所有运行期路径都支持环境变量覆盖，无需改任何脚本：

| 变量 | 含义 | 默认 |
|---|---|---|
| `AUTOKNIT_HOME` | 安装根目录 | `~/.autoknit` |
| `FW1` | 框架根路径 | `$AUTOKNIT_HOME/framework-v1` |
| `DSH_BIN` | dsh 可执行文件绝对路径 | `~/Library/Application Support/QClaw/npm-global/bin/dsh` |
| `DSH_HOME` | dsh 运行环境目录 | `~/.fw-dsh`（与其他 agent 隔离） |
| `FW_PY` | fw-new 使用的 python | venv 内的 python3.11 |

## 卸载

```bash
rm -rf ~/.autoknit ~/.local/bin/autoknit
```

## 已知边界

- `fw-merge` / `fw-data-bridge` 目前无 `pyproject.toml`，不参与 pip 安装（源码随框架一并复制，`autoknit merge` 走源码 `PYTHONPATH` 直跑）。
- `autoknit new` / `run`（非 demo 模式）依赖 dsh 已安装并登录；`demo` 模式零 token 不需要 dsh。