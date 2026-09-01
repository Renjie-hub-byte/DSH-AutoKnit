# e2e/ —— 需求7 端到端示例 + 复现指南

> 目的：给 auditor 提供"零写入、可独立复现"的端到端证据链路径。
> 主目标产物：`e2e/task.yaml`（3 模块任务书）+ `e2e/drivers/`（executor/auditor 脚本化驱动）
> + `e2e/run_e2e.py`（编排器，全流程自检）+ `e2e/runs/`（每次运行的真实产物，含归档树）。

## 一、本目录文件

| 文件 | 作用 |
|---|---|
| `task.yaml` | 3 模块任务书（钻石依赖 m01→m02/m03；预算 max_tokens=1500 warn_at=0.7；max_parallel=3；retry_before_switch=2）|
| `drivers/e2e-executor.py` | 脚本化 executor：开工先读 REVIEW → 列 todo → 干活 → 自测；m02/E1 阶段"契约声明产物未落盘"制造升级链；每轮上报 tokens |
| `drivers/e2e-auditor.py` | 脚本化 auditor：过程审计三步 + 结果对照（契约产物存在性）；判定四段机器可解析；每轮 50 tokens |
| `run_e2e.py` | 编排器：fw-protocol CLI → fw-scaffold CLI → fw-runner API（真实 BudgetGate + FwIntegrateHook）→ 证据自检 22 项 → fw-budget status → fw-integrate complete 归档 |
| `runs/` | 真实运行产物（每次运行独立 `run-<时间戳>/` 目录，可重复执行零冲突）|

## 二、auditor 独立复现路径（零写入已审计六模块）

```bash
cd ~/projects-hold/projects/dsh-workflow/framework-v1

# 1) 校验任务书（应 exit 0 / ok=true / status=pass）
python3.11 fw-protocol/bin/fw-protocol e2e/task.yaml --json

# 2) 全流程端到端（含证据自检；新运行目录 e2e/runs/run-<ts>/；退出码 0=全部通过）
python3.11 e2e/run_e2e.py
python3.11 e2e/run_e2e.py --json      # 机器可解析摘要

# 3) 复核归档产物
#    最新：e2e/runs/run-<最新时间戳>/archived/任务-端到端-订单管道_2026-08-21-*/ 下含：
#    完成报告.md（匹配/缺失/clean 清单 + end_gate 决定）+ ARCHIVE.md
#    总日志/快照.json（status=archived cause=completed schema_version=3）
#    总日志/dispatch.jsonl（事件 seq 严格单调；module.blocked ×2；executor.switch；budget.warn；integration.check）
#    modules/m02-数据清洗/REVIEW.md（status=done, block_total=2, executor_switches=1, executor_id=E2, executor_round=3）
#    modules/m02-数据清洗/logs/handover-E2-*.md（交接三件套）
```

独立复现要点（auditor 不在已审计模块目录写入即可，全部新增在 e2e/ 下）：
- 每次 `run_e2e.py` 生成独立时间戳目录，与现有 runs/ 产物零冲突、无需 --force（存档证据链不被污染）。
- `fw-budget status` 与 `fw-integrate complete` 均为只读 + 归档收尾（auditor 可自行在 /tmp 另建任务根验证，见各模块 spec）。

## 三、自我保护：对已审计六模块零源码修改

本轮（需求7）新增文件仅限：`e2e/`（本目录）、`framework-v1/README.md`、`framework-v1/design-v04.md`。
可在复现前后用以下命令核对基线与现状一致（= 未修改任何已审计模块源码）：

```bash
# 复现前：保存基线
find fw-protocol fw-scaffold fw-runner fw-budget fw-integrate presets \
  -type f \( -name "*.py" -o -name "*.yaml" -o -name "*.yml" -o -name "*.json" -o -name "*.md" \) \
  ! -path "*/__pycache__/*" ! -path "*/.pytest_cache/*" | sort | xargs shasum > /tmp/fw1_base.txt
# 复现后：diff（应无差异；如出现差异请立即报告）
find fw-protocol fw-scaffold fw-runner fw-budget fw-integrate presets \
  -type f \( -name "*.py" -o -name "*.yaml" -o -name "*.yml" -o -name "*.json" -o -name "*.md" \) \
  ! -path "*/__pycache__/*" ! -path "*/.pytest_cache/*" | sort | xargs shasum > /tmp/fw1_after.txt
diff /tmp/fw1_base.txt /tmp/fw1_after.txt && echo "ZERO-MODIFICATION OK"
```

## 四、当前运行产物（本轮真实执行，2026-08-21）

| 运行目录 | 结果 |
|---|---|
| `e2e/runs/run-20260821-144536/` | 首次运行：22 项证据自检全 PASS；归档 `archived/任务-端到端-订单管道_2026-08-21-20260821-144537/` |
| `e2e/runs/run-20260821-144928/` | 复现运行（可重复性证明）：22 项全 PASS；归档 `archived/任务-端到端-订单管道_2026-08-21-20260821-144929/` |

证据摘要（详见 `e2e/runs/run-20260821-144536/e2e-evidence.md`）：
- run_id=`run-20260821-144537-fddc3316`，status=complete，32 事件，tokens_used=1300，耗时 ≈0.56s
- 并行 active_max=3 ≤ max_parallel=3；m02/m03 等待 m01 done 后才 dispatch
- m02 升级链：module.blocked ×2（action retry→switch）→ executor.switch（handover bundle）→ E2 第 3 轮通过
- 预算：budget.warn（used=1300/1500=0.867）回合；fw-budget status phase=warned，排行 [m02:750, m01:300, m03:250]
- 集成：FwIntegrateHook passed；基线 matched=3/missing=0/clean=2/violation=0；integration.check 事件落盘
- 归档：完成报告.md + ARCHIVE.md + 快照 archived/completed

## 五、已知限制（e2e 专项）

1. executor/auditor 为确定性脚本化 agent（模拟真实 LLM agent 行为位），用于机制验证；接入真实 LLM = 把 `run_e2e.py` 中 `ScriptedAgentDriver` 的 cmd 换成 presets/ 下的真实 agent 命令。
2. `--mode` 默认 speed_first；cost_first 差异（max_parallel→min(…,2)、retry_before_switch+1）在 README §五 文档化，未在 e2e 里重复跑。
3. 预算路径本轮演示 **warn**（设计允许 warn 或 stop/add-budget resume 二选一）；stop/add-budget/resume 已由 round_005 审计覆盖（提供独立证据）。
4. token 记账走本地事件流账本（`DshTokenMeter._query_dsh()` stub → fallback），真实 dsh meter 接入点见 `fw-budget/fw_budget/meter.py`。
5. GUI（三 preset 可选）与 git 推送不在本目录范围（收尾流程，见 `design-v04.md` §六）。
