#!/usr/bin/env bash
# fw-new.sh —— 从 PRD 一键生成可分治执行的任务（对标 lh 的"给个文件就干"）
# 用法:
#   fw-new.sh <PRD.md> [--name 任务名] [--owner 负责人] [--out 输出目录]
#
# 流程:
#   1. headless planner 读 PRD → 产出规划 JSON（planner-raw.json，宽松格式）
#   2. fw-normalize 程序接管结构 → 标准 task.yaml（字段归位/全角规范化/模块转数组）
#   3. fw-protocol 校验 → fw-scaffold 生成任务目录树
#   4. 打印任务目录路径 → 之后直接 fw-run <任务目录>
#
# 前置:
#   - dsh headless（planner 真身）
#   - fw-protocol / fw-scaffold 可用（framework-v1 兄弟包）
set -uo pipefail

FW1="${FW1:-$HOME/projects-hold/projects/dsh-workflow/framework-v1}"
SCAFFOLD="$FW1/fw-scaffold/bin/fw-scaffold"
DSH_BIN="${DSH_BIN:-$HOME/Library/Application Support/QClaw/npm-global/bin/dsh}"
FW_PY="${FW_PY:-python3.11}"   # 需含 yaml（fw-new 人确认段同用）

# 独立 DSH_HOME：fw 专用环境，不碰主 ~/.dsh（小理等 agent 用 pro 不受影响）
FW_DSH_HOME="${FW_DSH_HOME:-$HOME/.fw-dsh}"
export DSH_HOME="$FW_DSH_HOME"

[ $# -lt 1 ] && echo "用法: fw-new.sh <PRD.md> [--name 任务名] [--owner 负责人] [--out 目录]" && exit 1
PRD="$1"; shift
NAME=""; OWNER="杰哥"; OUT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --name) NAME="$2"; shift 2;;
    --owner) OWNER="$2"; shift 2;;
    --out) OUT="$2"; shift 2;;
    *) echo "✗ 未知参数: $1"; exit 1;;
  esac
done

[ -f "$PRD" ] || { echo "✗ PRD 文件不存在: $PRD"; exit 1; }
PRD="$(cd "$(dirname "$PRD")" && pwd)/$(basename "$PRD")"
NAME="${NAME:-$(basename "$PRD" .md)}"
OUT="${OUT:-$FW1}"

WORK="/tmp/fw-new-$$"
mkdir -p "$WORK"
PLAN_TASK="$WORK/PLAN_TASK.md"
PLANNER_RAW="$WORK/planner-raw.json"   # planner 直接产出（宽松 JSON）
TASK_YAML="$WORK/task.yaml"            # fw-normalize 程序化产出（标准 YAML）

PLANNER_MD_BASE="$(cat "$FW1/prompts/planner.md")"

echo "════════════════════════════════════════════"
echo "  fw-new  —  规划 $NAME"
echo "  PRD     : $PRD"
echo "════════════════════════════════════════════"

# planner 也用 flash（默认 deepseek-official），避免 pro+high reasoning 拆模块过慢
FW_PLANNER_PROVIDER="${FW_PLANNER_PROVIDER:-deepseek-official}"
FW_PLANNER_MODEL="${FW_PLANNER_MODEL:-deepseek-v4-flash}"
cat > "$WORK/planner-patch.yml" <<PATCHEOF
- id: agent-default-model
  config:
    provider: $FW_PLANNER_PROVIDER
    model: $FW_PLANNER_MODEL
PATCHEOF

# 打回重跑循环：validate 检测到语义错误（接口重复/依赖环等）→ 把错误喂回 planner 修正
MAX_PLAN_RETRY="${MAX_PLAN_RETRY:-2}"   # 除首次外最多再重试 2 次
FEEDBACK=""
ATTEMPT=0
while true; do
  ATTEMPT=$((ATTEMPT + 1))

  # --- 组装 planner 提示词（含上一轮校验反馈）---
  PLANNER_MD="$PLANNER_MD_BASE"
  PLANNER_MD="${PLANNER_MD//\{PRD\}/$PRD}"
  PLANNER_MD="${PLANNER_MD//\{NAME\}/$NAME}"
  PLANNER_MD="${PLANNER_MD//\{OWNER\}/$OWNER}"
  PLANNER_MD="${PLANNER_MD//\{PLANNER_RAW\}/$PLANNER_RAW}"
  PLANNER_MD="${PLANNER_MD//\{SOURCE_PRD\}/$(basename "$PRD")}"
  PLANNER_MD="${PLANNER_MD//\{DATE\}/$(date +%Y-%m-%d)}"
  if [ -n "$FEEDBACK" ]; then
    PLANNER_MD="${PLANNER_MD}

【上一轮规划被校验拒绝——必须修正后再产出（最高优先）】
$FEEDBACK
"
  fi
  cat > "$PLAN_TASK" << TASKEOF
$PLANNER_MD
TASKEOF

  echo "[fw-new] planner (dsh headless) 第 $ATTEMPT 次产出规划 JSON…"
  "$DSH_BIN" --profile headless --patch "$WORK/planner-patch.yml" "$(cat "$PLAN_TASK")" > "$WORK/planner.log" 2>&1

  if [ ! -s "$PLANNER_RAW" ]; then
    echo "✗ planner 未产出 planner-raw.json（日志尾部见下）"
    tail -15 "$WORK/planner.log"
    exit 1
  fi
  echo "[fw-new] 规划 JSON 已产出（$(wc -l < "$PLANNER_RAW") 行），fw-normalize 接管结构…"

# fw-normalize：AI 只产内容，程序接管结构（补全 id/layer/meta/默认值 + 容错归位）
"$FW_PY" "$FW1/fw-tools/fw-normalize.py" "$PLANNER_RAW" -o "$TASK_YAML" \
    --name "$NAME" --owner "${OWNER:-}" \
    --source-prd "$(basename "$PRD")" --created "$(date +%Y-%m-%d)"
NORM_RC=$?
if [ $NORM_RC -eq 1 ]; then
  echo "✗ fw-normalize 无法修复 planner 产物（见上）"
  exit 1
fi
if [ ! -s "$TASK_YAML" ]; then
  echo "✗ task.yaml 生成失败"
  exit 1
fi
echo "[fw-new] task.yaml 已生成（$(wc -l < "$TASK_YAML") 行）"

# 人确认（仅第一次；重试是自动修正，不再问）
# v2(2026-09-02): 场景自适应——交互终端挂起等人审（语义=窗口挂着答复了继续）；
# 非交互环境（定时/自动化，stdin 非 TTY）不悬挂：提案已落盘 task.yaml，scaffold 校验兜底后自动继续
if [ "$ATTEMPT" -eq 1 ] && [ "${FW_SKIP_CONFIRM:-0}" != "1" ] && [ -t 0 ]; then
  echo ""
  echo "════════ 规划提案（请审阅）════════"
  "$FW_PY" - "$TASK_YAML" <<'PYEOF'
import sys, yaml
doc = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
print("模块规划：")
for m in (doc.get("modules") or []):
    env = m.get("environment") or {}
    pkgs = ", ".join(env.get("python_packages") or [])
    deps = ", ".join(m.get("dependencies") or []) or "无"
    tail = f"  [依赖包: {pkgs}]" if pkgs else ""
    print(f"  {m['id']} {m.get('name')}（依赖 {deps}）{tail}")
pkgs, tools = set(), set()
for m in (doc.get("modules") or []):
    env = m.get("environment") or {}
    pkgs.update(env.get("python_packages") or [])
    tools.update(env.get("system_tools") or [])
print("环境声明（聚合）：")
print(f"  python_packages: {', '.join(sorted(pkgs)) or '无'}")
print(f"  system_tools: {', '.join(sorted(tools)) or '无'}")
PYEOF
  echo "════════════════════════════════════"
  read -p "认可这个规划吗？[回车=确认 / n=取消]: " ans
  if [ "$ans" = "n" ] || [ "$ans" = "N" ]; then
    echo "已取消。可修改 PRD 或补充需求后重跑 fw-new。"
    exit 1
  fi
  echo "✅ 已确认，继续生成任务目录…"
elif [ "$ATTEMPT" -eq 1 ]; then
  echo "✅ 无人值守模式：提案已归档（task.yaml），scaffold 校验兜底，自动继续…"
fi

  # fw-protocol 校验（scaffold 内部会校验，这里直接跑 scaffold）
  echo "[fw-new] fw-scaffold 校验并生成任务目录（父目录=${OUT}）…"
  cd "$FW1/fw-scaffold" && "$FW_PY" bin/fw-scaffold --output "$OUT" "$TASK_YAML" > "$WORK/scaffold.log" 2>&1
  RC=$?
  tail -8 "$WORK/scaffold.log"
  if [ $RC -eq 0 ]; then
    break   # 校验通过，出循环
  fi

  # 校验失败：提取错误喂回 planner，重试
  FEEDBACK="$(grep -E "interface_duplicate|依赖环|schema|校验失败|interface" "$WORK/scaffold.log" | head -20)"
  if [ "$ATTEMPT" -ge "$MAX_PLAN_RETRY" ]; then
    echo "✗ 校验 $ATTEMPT 次仍不通过，请人工介入（错误见上）"
    exit 1
  fi
  echo "[fw-new] 校验未通过（第 $ATTEMPT 次），把错误喂回 planner 重写…"
done

# 定位生成的任务目录（OUT 下最新的 任务-<name>_YYYY-MM-DD）
TASK_DIR=$(ls -dt "$OUT"/任务-* 2>/dev/null | head -1)
if [ -z "$TASK_DIR" ] || [ ! -f "$TASK_DIR/task.yaml" ]; then
  echo "✗ 无法定位生成的任务目录（$OUT 下无 任务-*）"
  exit 1
fi
echo ""
echo "✅ 任务目录已生成: $TASK_DIR"
echo "   接下来直接跑: fw-run \"$TASK_DIR\""
rm -rf "$WORK"
exit 0