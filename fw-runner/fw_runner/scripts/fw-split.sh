#!/usr/bin/env bash
# fw-split.sh —— framework-v1 split agent 包装器（子进程形态）
# 被 fw-runner 以默认 split 驱动调用（split.py call_split_agent → ScriptedAgentDriver role=split）
# cwd = 模块目录。
#
# split agent 真身 = dsh --profile headless（flash 模型，FW_SPLIT_MODEL 覆盖）
#
# 职责：
#   1. 读 runner 收集的 split 上下文 tmp/split-context.json（5 项输入，call_split_agent 已写）
#   2. 预检：上下文合法 + 叶子模块不拆（交付物 ≤ split_min_deliverables，防打架，设计第五节防护）
#   3. 拼装"模块拆分指令"（prompts/split.md 提示词逻辑）→ 交给 dsh headless 一次性拆分
#   4. 提取 agent 输出的拆解 JSON → 写 tmp/split-outcome.json 的 detail.split 供 runner 解析
#
# 协议（与 split.py D2 / 工程对接清单一.1 对齐）：
#   退出码 0 + tmp/split-outcome.json 存在 → 成功（detail.split 含拆解 JSON）
#   退出码 13 → 中断（resume）；退出码 != 0 → SplitCallError（回人不硬拆）
#
# 环境变量（runner 注入）：MODULE_DIR TASK_ROOT RUN_ID ROUND ROLE EXECUTOR_ID MODE
# 可配置：FW_SPLIT_MODEL / FW_SPLIT_MODE(dsh|demo) / FW_SPLIT_TIMEOUT / FW_SPLIT_MIN_DELIVERABLES
#          FW_SPLIT_PROMPT（提示词路径覆盖） / DSH_BIN
set -uo pipefail

MODULE_DIR="${MODULE_DIR:?}"
TASK_ROOT="${TASK_ROOT:?}"
RUN_ID="${RUN_ID:-run}"
ROUND="${ROUND:-1}"
EXECUTOR_ID="${EXECUTOR_ID:-split}"
FW_SPLIT_MODEL="${FW_SPLIT_MODEL:-deepseek-v4-flash}"
FW_SPLIT_MODE="${FW_SPLIT_MODE:-dsh}"
FW_SPLIT_TIMEOUT="${FW_SPLIT_TIMEOUT:-300}"
FW_SPLIT_MIN_DELIVERABLES="${FW_SPLIT_MIN_DELIVERABLES:-2}"
# 提示词解析（打包后可用，不依赖 monorepo 布局）：
#   默认 = 包内 prompts/split.md（脚本自身位置的上一级 fw_runner/prompts/；源码树与 wheel 安装后均成立）
#   FW_SPLIT_PROMPT 仍可覆盖（runner 侧已默认注入包内提示词路径；仓库根 prompts/split.md 可用该变量指回）
FW_SPLIT_PROMPT="${FW_SPLIT_PROMPT:-$(CDPATH= cd -- "$(dirname -- "$0")/../prompts" && pwd)/split.md}"

cd "$MODULE_DIR" || exit 2
mkdir -p tmp
OUTCOME="tmp/split-outcome.json"
CONTEXT_FILE="tmp/split-context.json"

echo "[fw-split] module=$(basename "$MODULE_DIR") exec=$EXECUTOR_ID round=$ROUND mode=$FW_SPLIT_MODE model=$FW_SPLIT_MODEL"

# ---------- 1. 读上下文 + 预检（叶子模块不拆） ----------
python3 - "$CONTEXT_FILE" "$FW_SPLIT_MIN_DELIVERABLES" <<'PYEOF'
import json, sys
path, min_d = sys.argv[1], int(sys.argv[2])
try:
    with open(path, "r", encoding="utf-8") as f:
        ctx = json.load(f)
except Exception as e:
    print(f"[fw-split] ✗ 读 split-context.json 失败: {e}", file=sys.stderr)
    sys.exit(2)
if not isinstance(ctx, dict):
    print("[fw-split] ✗ split-context.json 不是 JSON 对象", file=sys.stderr)
    sys.exit(2)
for k in ("mid", "objective", "deliverables", "review", "files"):
    if k not in ctx:
        print(f"[fw-split] ✗ split-context.json 缺字段: {k}", file=sys.stderr)
        sys.exit(2)
deliv = [str(x).strip() for x in (ctx.get("deliverables") or []) if str(x).strip()]
if len(deliv) <= min_d:
    print(f"[fw-split] ✗ 交付物 {len(deliv)} ≤ {min_d}（叶子模块不拆，防打架），回人决策", file=sys.stderr)
    sys.exit(3)
PYEOF
rc=$?
[ "$rc" -eq 0 ] || exit "$rc"

# ---------- 2. 拼装 split 指令（自包含，prompts/split.md 逻辑 + 5 项输入） ----------
if [ ! -f "$FW_SPLIT_PROMPT" ]; then
  echo "[fw-split] ✗ 缺提示词: $FW_SPLIT_PROMPT" >&2
  exit 2
fi
python3 - "$FW_SPLIT_PROMPT" "$CONTEXT_FILE" <<'PYEOF'
import json, sys
prompt_path, context_path = sys.argv[1], sys.argv[2]
prompt = open(prompt_path, encoding="utf-8").read()
ctx = json.load(open(context_path, encoding="utf-8"))
deliv = [str(x).strip() for x in (ctx.get("deliverables") or []) if str(x).strip()]
remaining = ctx.get("remaining_items") or []
files = ctx.get("files") or []
wnh = [str(x) for x in (ctx.get("will_not_have") or [])]
mrem = ctx.get("module_remaining") or {}
task = f"""{prompt}

【本模块拆分上下文】（由 runner 收集，仅本模块；绝对路径以 {ctx.get('mid')} 为准）
模块 id：{ctx.get('mid')}
任务总目标：{ctx.get('task_goal') or '(未声明)'}
明确不做 will_not_have：{wnh or '(无)'}
objective：{ctx.get('objective')}
父模块剩余量估计：scope={mrem.get('scope') or '(未知)'} estimate_lines={mrem.get('estimate_lines') or '(未知)'}
交付物清单（完整，含未勾选）：{deliv}
Auditor 最近判定：passed={ctx.get('passed_count')} total={ctx.get('total_count')} remaining={remaining}
REVIEW.md 全文：
{ctx.get('review') or '(无)'}
已完成文件（绝对路径）：
{chr(10).join(files) if files else '(无)'}
父模块依赖（上游）：{ctx.get('dependencies') or []}
split_depth：{ctx.get('split_depth')}   executor_round：{ctx.get('executor_round')}   model_tier：{ctx.get('model_tier')}

请严格按上面提示词输出合法 JSON（v2 协议：next_block 单块 + remaining_after；只输出 JSON，不要 markdown 代码块标记）。"""
open("tmp/SPLIT_TASK.md", "w", encoding="utf-8").write(task)
print(f"[fw-split] split 指令已拼装 → tmp/SPLIT_TASK.md（交付物 {len(deliv)} 项，提示词 {prompt_path}）")
PYEOF
rc=$?
[ "$rc" -eq 0 ] || exit "$rc"

# ---------- 3. 执行（dsh headless = split agent 真身，一次性） ----------
case "$FW_SPLIT_MODE" in
  demo)
    # 链路联调：写一个最小可用的拆解 JSON（v2：next_block 单块 + remaining_after），不调 dsh
    python3 - "$OUTCOME" "$CONTEXT_FILE" <<'PYEOF'
import json, sys
outcome, context_file = sys.argv[1], sys.argv[2]
ctx = json.load(open(context_file, encoding="utf-8"))
mid = str(ctx.get("mid") or "m00")
deliv = [str(x).strip() for x in (ctx.get("deliverables") or []) if str(x).strip()]
half = max(1, len(deliv) // 2)
split_json = {
    "action": "split",
    "parent_module": mid,
    "next_block": {
        "id": f"{mid}a",
        "name": "下一块",
        "objective": f"[{ctx.get('task_goal') or mid}] {mid}a 第一步：{deliv[0] if deliv else '核心功能'}",
        "deliverables": (deliv[:half] or [deliv[0]]) if deliv else [f"{mid}a 交付物"],
        "files": [f"src/{mid}a.py"],
    },
    "remaining_after": {
        "scope": "; ".join(deliv[half:]) if deliv else "剩余：后续块",
        "estimate_lines": 600,
    },
    "dependency_map": {f"{mid}a": []},
    "context_from_parent": "demo 驱动：链路联调用拆分结果（未真实拆分）",
}
json.dump({"status": "ok", "verdict": "", "root": "",
           "detail": {"split": split_json},
           "reason": "demo 驱动（链路联调）"},
          open(outcome, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(f"[fw-split] demo 完成 → {outcome}")
PYEOF
    rc=$?
    [ "$rc" -eq 0 ] || exit "$rc"
    exit 0
    ;;
  dsh)
    ;;
  *)
    echo "[fw-split] ✗ 未知模式: $FW_SPLIT_MODE" >&2
    exit 2
    ;;
esac

DSH_BIN="${DSH_BIN:-$HOME/Library/Application Support/QClaw/npm-global/bin/dsh}"
if [ ! -x "$DSH_BIN" ]; then
  echo "[fw-split] ✗ dsh 未就绪: $DSH_BIN" >&2
  exit 2
fi

ROLE_TASK="$(cat tmp/SPLIT_TASK.md)"
: > tmp/split_output.txt
FW_SPAWN="$(dirname "$0")/fw-spawn.py"
echo "[fw-split] 调 dsh headless 拆分（model=${FW_SPLIT_MODEL}）…"

# 可选：指定 split 模型（flash 档）→ 生成 patch 覆盖 agent-default-model
# provider 可配：默认 deepseek-official，可用 FW_SPLIT_PROVIDER 覆盖
# reasoningEffort 默认不设：flash 不支持；只有 pro 支持 → 显式设 FW_SPLIT_REASONING 才加
DSH_PATCH_ARG=""
FW_PROVIDER="${FW_SPLIT_PROVIDER:-deepseek-official}"
FW_REASONING="${FW_SPLIT_REASONING:-}"
REASONING_LINE=""
[ -n "$FW_REASONING" ] && REASONING_LINE="    reasoningEffort: $FW_REASONING"
if [ -n "$FW_SPLIT_MODEL" ]; then
  cat > tmp/model-patch.yml <<PATCHEOF
- id: agent-default-model
  config:
    provider: ${FW_PROVIDER}
    model: ${FW_SPLIT_MODEL}
${REASONING_LINE}
PATCHEOF
  DSH_PATCH_ARG="--patch tmp/model-patch.yml"
fi

python3 "$FW_SPAWN" -- "$DSH_BIN" --profile headless $DSH_PATCH_ARG "$ROLE_TASK" \
    --out tmp/split_output.txt --timeout "$FW_SPLIT_TIMEOUT" \
    --cwd "$MODULE_DIR" &>/dev/null &
SPAWN_PID=$!
wait $SPAWN_PID 2>/dev/null

# ---------- 4. 提取拆解 JSON → 写 outcome ----------
python3 - "$OUTCOME" "$CONTEXT_FILE" <<'PYEOF'
import json, sys
outcome, context_file = sys.argv[1], sys.argv[2]
try:
    text = open("tmp/split_output.txt", encoding="utf-8").read()
except Exception as e:
    print(f"[fw-split] ✗ 读 dsh 输出失败: {e}", file=sys.stderr)
    sys.exit(2)

def _extract(text):
    # 去掉 markdown 代码块围栏后，扫描所有平衡 JSON 对象
    lines = []
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("```"):
            continue
        lines.append(ln)
    text = "\n".join(lines)
    cands, i = [], 0
    while True:
        start = text.find("{", i)
        if start < 0:
            break
        depth, in_str, esc, end = 0, False, False, -1
        for j in range(start, len(text)):
            ch = text[j]
            if in_str:
                if esc: esc = False
                elif ch == "\\": esc = True
                elif ch == '"': in_str = False
                continue
            if ch == '"': in_str = True
            elif ch == "{": depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = j
                    break
        if end < 0:
            i = start + 1
            continue
        cands.append(text[start:end + 1])
        i = end + 1
    parsed = []
    for c in cands:
        try:
            d = json.loads(c)
            if isinstance(d, dict):
                parsed.append(d)
        except Exception:
            pass
    # 取最后一个含 action 的对象（拆分决策）——agent 通常最后给出最终 JSON
    for d in reversed(parsed):
        if isinstance(d.get("action"), str):
            return d
    return parsed[-1] if parsed else None

ctx = json.load(open(context_file, encoding="utf-8"))
split_json = _extract(text)
if split_json is None:
    print("[fw-split] ✗ dsh 输出中未找到拆解 JSON（详见 tmp/split_output.txt 尾部）", file=sys.stderr)
    tail = text.strip().splitlines()[-8:]
    for ln in tail:
        print("  " + ln, file=sys.stderr)
    sys.exit(3)

json.dump({"status": "ok", "verdict": "", "root": "",
           "detail": {"split": split_json},
           "reason": f"dsh headless split agent 输出（{ctx.get('mid')}）"},
          open(outcome, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(f"[fw-split] 完成，拆解 JSON → {outcome}（action={split_json.get('action')}）")
PYEOF
rc=$?
[ "$rc" -eq 0 ] || exit "$rc"
exit 0
