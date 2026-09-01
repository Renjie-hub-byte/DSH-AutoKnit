#!/usr/bin/env bash
# AutoKnit run —— 分治执行一键启动（前身 framework-v1/fw-run.sh）
# 品牌：AutoKnit 对外 CLI；内部包名 fw_* 保留（渐进品牌化，零 break）
# 用法:
#   fw-run.sh <任务目录> [--mode demo|dsh] [--max-parallel N] [--resume] [--watch]
#             [--executor-model 模型] [--auditor-model 模型] [--config dflow.yaml]
#
# 说明:
#   任务目录 = fw-scaffold 生成的 任务-<名>_<日期>/（内含 task.yaml + modules/）
#   demo  = 内置假 executor/auditor（链路联调，0 token）
#   dsh   = 咱们自己的 executor（dsh headless；默认）真实干活
#   --watch = 跑完自动跟 fw-status 看结果
#   --resume = 从检查点续跑
#   --executor-model / --auditor-model = 指定角色用哪个模型（如 doubao-seed-2.0-mini 低成本档）
#      不指定则用 dsh 默认模型（GUI 完全不受影响，只作用于本任务的角色）
#   --config dflow.yaml = 项目级配置（runtime 阈值/并行 + models 各角色 model/reasoning_effort/provider），
#      自动向上查找（任务目录→cwd→父目录）；CLI flag 覆盖 yaml，yaml 覆盖默认。
set -uo pipefail

FW1="${FW1:-$HOME/projects-hold/projects/dsh-workflow/framework-v1}"
BIN="$FW1/fw-runner/bin"
RUNNER="$BIN/fw-runner"   # bugfix: 此前用未赋值的 $RUNNER，set -u 下 autoknit run 必崩。指向真正入口 bin/fw-runner

# 独立 DSH_HOME：fw 专用环境，不碰主 ~/.dsh（小理等 agent 用 pro 不受影响）
# 默认 ~/.fw-dsh（含独立 settings.yaml=flash + 独立 credentials=新 key）；可 FW_DSH_HOME 覆盖
FW_DSH_HOME="${FW_DSH_HOME:-$HOME/.fw-dsh}"
export DSH_HOME="$FW_DSH_HOME"

# sandbox 模式（BUG-002 修复）：macOS 上 sandbox-exec 已坏（sandbox_apply: Operation not permitted），
# workspace-write 会让 executor/auditor 的所有 bash 命令 fail closed（pytest 跑不了 → L1 证据拿不到）。
# danger-full-access = dsh 官方兜底（"不作限制，绝不咨询提供方"，approval 自动 never，不走 sandbox-exec）。
# 危险面可控：executor 跑在框架自建临时任务目录 + fw-trace.py 路径级越界检测兜底。
export DSH_PERMISSION_MODE="${DSH_PERMISSION_MODE:-danger-full-access}"

# 无论正常/中断退出，清理可能残留的 spawn/headless 孤儿（防下次堆积吃资源）
cleanup() {
  ps aux | grep -E "[f]w-spawn|[d]sh --profile headless" | awk '{print $2}' | while read -r pid; do
    kill -9 "$pid" 2>/dev/null
  done
}
trap cleanup EXIT

[ $# -lt 1 ] && echo "用法: fw-run.sh <任务目录> [--mode demo|dsh] [--max-parallel N] [--resume] [--watch] [--executor-model 模型] [--auditor-model 模型] [--config dflow.yaml]" && exit 1
TASK_DIR="$1"; shift
MODE="dsh"; MAXP=""; RESUME=""; WATCH=""; EXEC_MODEL=""; AUD_MODEL=""; CONFIG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --mode) MODE="$2"; shift 2;;
    --max-parallel) MAXP="$2"; shift 2;;
    --resume) RESUME="--resume-from-checkpoint"; shift;;
    --watch) WATCH="1"; shift;;
    --executor-model) EXEC_MODEL="$2"; shift 2;;
    --auditor-model) AUD_MODEL="$2"; shift 2;;
    --config) CONFIG="$2"; shift 2;;
    *) echo "✗ 未知参数: $1"; exit 1;;
  esac
done

[ -d "$TASK_DIR" ] || { echo "✗ 任务目录不存在: $TASK_DIR"; exit 1; }
[ -f "$TASK_DIR/task.yaml" ] || { echo "✗ 不是任务目录（缺 task.yaml）: $TASK_DIR"; exit 1; }
# 转绝对路径（后续 cd 到 fw-runner 后仍可用）
TASK_DIR="$(cd "$TASK_DIR" && pwd)"

# 环境预置（2026-08-25，杰哥拍板）：角色需要的能力任务开始时配好——venv+依赖+命令探测，
# 产出 tmp/env-manifest.json 供 executor/auditor 指令注入（可用命令必须真跑，不可用不得假装）
echo "── 环境预置（fw-env）──"
bash "$BIN/fw-env-bootstrap.sh" "$TASK_DIR" || { echo "✗ 环境预置失败，中止"; exit 1; }

# ---- AutoKnit 数据桥（fw-api serve）拉起：随 autoknit run 启动，已有则复用 ----
# 面板 client 插件从 http://127.0.0.1:8765/api 拉任务；此处确保桥在（launchd 常驻或
# run-bridge-demo start 幂等拉起），跑完数据实时可读。具体 task 目录由各 run 的状态决定。
if [ -f "$FW1/fw-data-bridge/run-bridge-demo.sh" ] && [ "${AUTOKNIT_NO_BRIDGE:-}" != "1" ]; then
  if lsof -tiTCP:8765 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "  • 数据桥已运行 :8765（复用）"
  else
    echo "  • 数据桥未运行，拉起 fw-api serve :8765..."
    bash "$FW1/fw-data-bridge/run-bridge-demo.sh" start >/dev/null 2>&1 \
      && echo "  • 数据桥已启动 :8765" \
      || echo "  ⚠ 数据桥拉起失败（不影响运行，面板可能空）"
  fi
fi

# ---- 三通道配置合并（dflow.yaml < env < CLI）----
# 用 fw_runner.config 读 yaml + 合并 model env；CLI 模型参数最高优先级
MODEL_ENV_JSON=$(cd "$FW1/fw-runner" && python3.11 -m fw_runner.config_cli dump-env --cwd "$TASK_DIR" ${CONFIG:+--config "$CONFIG"} 2>/dev/null || echo "{}")
[ -n "$MODEL_ENV_JSON" ] && [ "$MODEL_ENV_JSON" != "{}" ] && \
  echo "$MODEL_ENV_JSON" | python3.11 -c "import sys,json,os; [os.environ.setdefault(k,v) for k,v in json.load(sys.stdin).items()]" 2>/dev/null || true

export FW_EXECUTOR_MODE="$MODE"
export FW_AUDITOR_MODE="$MODE"
# CLI 模型参数覆盖 yaml（若给了）
[ -n "$EXEC_MODEL" ] && export FW_EXECUTOR_MODEL="$EXEC_MODEL"
[ -n "$AUD_MODEL" ] && export FW_AUDITOR_MODEL="$AUD_MODEL"

echo "════════════════════════════════════════════"
echo "  fw-run  —  $TASK_DIR"
echo "  模式    : $MODE  并行: ${MAXP:-<runtime默认>}"
[ -n "$CONFIG" ] && echo "  配置    : $CONFIG"
[ -n "${FW_EXECUTOR_MODEL:-}" ] && echo "  executor 模型: $FW_EXECUTOR_MODEL"
[ -n "${FW_AUDITOR_MODEL:-}" ] && echo "  auditor 模型: $FW_AUDITOR_MODEL"
[ -n "${FW_PLANNER_MODEL:-}" ] && echo "  planner 模型: $FW_PLANNER_MODEL"
echo "════════════════════════════════════════════"

cd "$FW1/fw-runner" || exit 1
# 构造 runner CLI 参数（阈值/并行从 yaml 或 CLI）
RUN_ARGS=""
if [ -n "$MAXP" ]; then RUN_ARGS="$RUN_ARGS --max-parallel $MAXP"; fi
# 从 dflow.yaml runtime 读 split 阈值等 → 转 --flag（yaml→CLI 透传）
RUNTIME_FLAGS=$(python3.11 -m fw_runner.config_cli run-flags --cwd "$TASK_DIR" ${CONFIG:+--config "$CONFIG"} 2>/dev/null || true)
[ -n "$RUNTIME_FLAGS" ] && RUN_ARGS="$RUN_ARGS $RUNTIME_FLAGS"

# 防睡眠包裹（macOS）：长任务被系统睡眠冻结的防护（README 承诺的自动包裹）。
# 仅 macOS 且 caffeinate 可用时生效；非 macOS / 无 caffeinate 静默跳过，不改变原行为。
CAFF=""
if [ "$(uname -s)" = "Darwin" ] && command -v caffeinate >/dev/null 2>&1; then
  CAFF="caffeinate -i"
fi

PYTHONDONTWRITEBYTECODE=1 $CAFF $RUNNER run "$TASK_DIR" \
  --executor-cmd "bash $BIN/fw-executor.sh" \
  --auditor-cmd "bash $BIN/fw-auditor.sh" \
  $RUN_ARGS \
  $RESUME

CODE=$?
echo ""
# 启动失败不再沉默（2026-08-30 案例6）：非零退出时指明排查入口——哪个阶段崩了、日志在哪
if [ "$CODE" -ne 0 ]; then
  echo "✗ fw-run 失败（退出码 $CODE）。排查入口："
  echo "  • 事件流（最后一条=谁崩的）: $TASK_DIR/总日志/dispatch.jsonl"
  echo "  • executor 输出: $TASK_DIR/modules/*/tmp/executor_output.txt"
  echo "  • auditor 输出:  $TASK_DIR/modules/*/tmp/auditor_output_*.txt"
  echo "  • 环境清单（命令可用性）: $TASK_DIR/tmp/env-manifest.json"
  echo "  • executor/auditor 启动预检失败会直接打人话错误（缺 dsh 二进制/缺 DSH_HOME 凭据 → exit 2）"
fi
if [ -n "$WATCH" ] && [ -f "$TASK_DIR/总日志/快照.json" ]; then
  echo "── 结果速览（fw-status --once）──"
  fw-status "$TASK_DIR" --once
fi
exit $CODE