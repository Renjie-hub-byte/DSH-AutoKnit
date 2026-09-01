# framework-v1 快速上手（QUICKSTART）

> 给**任何会话/agent** 看的操作手册。遇到问题先查这里。
> 架构设计看 `README.md`，代码变更看 `CHANGELOG.md`，踩坑全集看 `fw-tools/运行手册-首跑记录.md`。

## 这是什么

framework-v1 = **分治执行框架**（规划共识 → 拆模块 → 并行执行 → 独立验收）。
executor/auditor/planner 全是 **dsh agent**（`dsh --profile headless`），不是 codex/lh。
比 lh 省 token（同 executor 迭代 + 上下文缓存复用，实测缓存命中 95%+）。

## 三件套（已软链到 ~/.local/bin）

```bash
fw-new <PRD.md> [--name 任务名] [--out 目录]   # 1. PRD → 任务目录（planner 自动拆模块）
fw-run <任务目录> [参数]                        # 2. 跑（executor+auditor 真身）
fw-status <任务目录> [--once]                   # 3. 看进度（事件驱动不轮询）
fw-token                                       # 4. 看 token 消耗（输入/输出/缓存命中）
```

## fw-run 参数速查

| 参数 | 说明 | 默认 |
|---|---|---|
| `--mode demo\|dsh` | demo=链路联调(0 token)；dsh=真实 agent | dsh |
| `--max-parallel N` | 最大并行模块数（模块多时调大提速） | 2 |
| `--resume` | 从快照续跑（已完成不重跑） | 关 |
| `--executor-model <模型>` | executor 用哪个模型（低成本档如 doubao-seed-2.0-mini） | dsh 默认 |
| `--auditor-model <模型>` | auditor 用哪个模型 | dsh 默认 |
| `--watch` | 跑完自动 fw-status | 关 |

**模块多怎么办**：不会全并行。依赖图拓扑分批 + `--max-parallel N` 控制。
**省 token**：加 `--executor-model doubao-seed-2.0-mini`（低成本档），GUI 其他 agent 不受影响。

## 结果怎么看

- `fw-status <任务目录>`：状态/模块明细/最近事件
- 模块目录 `modules/mXX-*/tmp/`：executor_output.txt / auditor_output_*.txt / **audit-result.json**（auditor 判定的 JSON）
- `总日志/快照.json`：模块 done/needs_human + 轮次 + 打回次数
- `总日志/dispatch.jsonl`：全量事件流（谁干了啥）

## 环境依赖（从零开始）

**Python 版本**：需要 **3.11+**（`python3.11 --version` 检查；uv 用户见下）。

**1. 安装框架三包**（fw-runner / fw-protocol / fw-scaffold，仓库内源码安装）：
```bash
cd framework-v1
python3.11 -m pip install -e ./fw-protocol -e ./fw-scaffold -e ./fw-runner
# 三件套命令（fw-new/fw-run/fw-status）已软链到 ~/.local/bin，没有就:
# ln -sf "$PWD/fw-tools/fw-new.sh" ~/.local/bin/fw-new 等
```

**2. 安装并登录 dsh CLI**（executor/auditor 真身）：
```bash
npm install -g dsh          # 或用 QClaw 内置路径（fw 默认找它）
dsh login                   # 登录后凭据写入 $DSH_HOME/.credentials.yaml
```
fw 默认用**独立** `DSH_HOME=~/.fw-dsh`（不碰主 ~/.dsh）。登录后若凭据写到了 ~/.dsh，
软链过去即可：`ln -s ~/.dsh/.credentials.yaml ~/.fw-dsh/.credentials.yaml`。
缺二进制/缺凭据时 fw-executor/fw-auditor 的启动预检会打人话错误（指明缺什么、路径、怎么装）。

**3. uv 用户注意**：uv 管理的 Python **不能直接 `pip install`**（没有 pip 模块），用：
```bash
uv venv --python 3.11 ~/.fw-runner-venv
uv pip install --python ~/.fw-runner-venv/bin/python -e ./fw-protocol -e ./fw-scaffold -e ./fw-runner
```
跑测试/命令用该 venv 的 python，或 `uv pip install` 装进当前项目的 venv。

## 长任务提示（macOS 必读）

**合盖/闲置睡眠会冻结运行中的会话**——executor/auditor 跑一半 Mac 睡着，任务就挂了。防法任选：
- 插电 + 保持开盖（系统设置里关掉"显示器关闭时自动睡眠"更稳）
- 用 `caffeinate -i` 包裹运行命令：`caffeinate -i fw-run <任务目录> ...`
- 打开一个常驻应用（如 QClaw）也可阻止系统睡眠

**数据桥（:8765）**：fw-run 会顺带拉起 fw-api serve :8765 给面板供数。**拉起失败只告警不阻塞运行**——
看到 `⚠ 数据桥拉起失败（不影响运行，面板可能空）` 属正常，面板为空不代表任务失败。

## 常见问题（遇到先查这）

**Q: 模块显示 needs_human 但产物存在？**
可能：auditor 判定没落盘（假打回）。查 `modules/mXX-*/tmp/audit-result.json` 和 `auditor_output_*.txt`——
如果报告里明确写了 pass/通过/无阻塞，但 json 缺失 → 是**假打回**（已用双保险根治，若再遇到看 auditor.sh 是否最新）。
如果 json 有且 pass → 快照状态未同步，可手动修正（见下）。

**Q: 怎么手动修正模块状态？**
```bash
# 编辑 总日志/快照.json：把 modules.mXX 改为 "done"，清 needs_human
# 然后 fw-run <任务目录> --resume 继续
```

**Q: token 用量一直是 0？**
framework 快照不统计（budget_used_tokens 恒 0）。用 `fw-token` 从会话文件读真实消耗。

**Q: executor/auditor 卡死/超时？**
- 检查孤儿进程：`ps aux | grep -E "[f]w-spawn|[d]sh --profile headless"`，有就 `pkill -9 -f` 清掉
- 超时环境变量：`FW_EXECUTOR_TIMEOUT`（默认240s）/`FW_AUDITOR_TIMEOUT`（默认420s）

**Q: 想换模型 / 降思考？**
- dsh 对 volcengine 配 reasoningEffort 会报 UNSUPPORTED（adapter 不声明）→ **别配**，换模型即可
- 低成本：`--executor-model doubao-seed-2.0-mini`；火山套餐模型查 `arkcli plans model-list --plan agent-plan`

**Q: 会话太多/磁盘涨？**
正常。每个 headless 子任务一个会话（executor/auditor 每轮各一个），是历史日志资产。
总大小可控（几个月 <1G），不用清理。

## 边界与纪律（重要）

- executor/auditor 已内置"纯代码验收，禁浏览器/截图/GUI"边界——AI 看图片/网页看不出代码对错，一律读文件+跑命令
- 任务目录必须绝对路径（fw-run 已自动处理）
- 改 framework 代码后跑回归：`cd framework-v1/fw-runner && python3.11 -m pytest tests/ -q`

## 当前状态（2026-08-22）

- ✅ 真实任务（ai 拓展资源库 v2）4 模块全 done，程序自主调度成立
- ✅ 假打回根治（auditor 判定双保险：json 优先 + 文本兜底）
- ✅ max-parallel 并行可控、token 明细可见（fw-token）
- 待优化：defineTool 强制 JSON（需 MCP，工程重，暂缓）；auditor 报告偏重（可减负）
