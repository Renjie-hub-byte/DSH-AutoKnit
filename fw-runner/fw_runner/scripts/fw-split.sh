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

# ---------- 1. 读上下文 + 预检（叶子模块不拆 → cannot_split 收尾块下放） ----------
python3 - "$CONTEXT_FILE" "$FW_SPLIT_MIN_DELIVERABLES" "$OUTCOME" <<'PYEOF'
import json, sys
path, min_d, outcome = sys.argv[1], int(sys.argv[2]), sys.argv[3]
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
    # BUG-20260903 修复：叶子模块预检原先 exit 3，被 runner 统一当作
    # "split agent 调用失败"回人，违背 2026-08-28 语义定稿（剩余量不多时
    # 由原 executor 收尾完成，不回人）。改为按协议写 cannot_split outcome
    # + 退出 0：call_split_agent 会抛 CannotSplitError，runner 程序化生成
    # 收尾块下放原 executor 继续完成剩余。
    print(f"[fw-split] ⓘ 交付物 {len(deliv)} ≤ {min_d}（叶子模块不拆，防打架）→ cannot_split 收尾块下放", file=sys.stderr)
    json.dump({"status": "ok", "verdict": "", "root": "",
               "detail": {"split": {"action": "cannot_split",
                                    "parent_module": str(ctx.get("mid") or "m00"),
                                    "reason": f"交付物 {len(deliv)} ≤ min {min_d}：叶子模块不拆，剩余由原 executor 收尾块完成"},
                          "_parse": {"source": "precheck", "layer": 0, "repaired": False,
                                     "candidates": 0, "parsed": 0, "truncated": False}},
               "reason": "叶子模块预检 → cannot_split 收尾"},
              open(outcome, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    sys.exit(0)
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
perr = ctx.get("protocol_errors") or []
pattempt = ctx.get("protocol_attempt") or 1
# U2（BUG-20260904）：崩溃/卡死路径 audit 缺席 → 剩余量是占位估算，不是真实判定。
# 提示词显式告知 split agent 保守拆分，防止占位 0 冒充"快完了"导致闭眼拆。
unknown_warn = ""
if ctx.get("remaining_unknown"):
    unknown_warn = (
        "⚠️ 警告：本模块 auditor 判定缺失（executor 崩溃/卡死路径），剩余量仅为占位估算，"
        "不可信。请按保守估计拆分（假设剩余工作可能远大于 estimate_lines 显示值），"
        "next_block.objective 里说明这是崩溃恢复拆分。\\n"
    )
feedback = ""
if perr:
    # 层③ 错误回传（2026-09-04 小澈复查）：把字段级协议错误原样喂回去让模型自改。
    # 一次 flash 回喂 ≪ 一次回人 + executor 从头重入（m05 实测三次重入 ~26 万 token）。
    feedback = f"""

【上次输出协议错误】（第 {pattempt} 次尝试，程序校验未通过，请**只修正下列字段**后重新输出完整 JSON）
{chr(10).join(f"- {e}" for e in perr)}
注意：字段必须存在且类型正确；id/name/objective 非空字符串；deliverables 是字符串数组；
remaining_after 是对象（收尾块写 {{"scope": "", "estimate_lines": 0}}）；
dependency_map 是对象，值必须是数组（如 {{"m05a": ["m04"]}}，不能写成裸字符串）。"""
task = f"""{prompt}

【本模块拆分上下文】（由 runner 收集，仅本模块；绝对路径以 {ctx.get('mid')} 为准）
模块 id：{ctx.get('mid')}
任务总目标：{ctx.get('task_goal') or '(未声明)'}
明确不做 will_not_have：{wnh or '(无)'}
objective：{ctx.get('objective')}
父模块剩余量估计：scope={mrem.get('scope') or '(未知)'} estimate_lines={mrem.get('estimate_lines') or '(未知)'}
交付物清单（完整，含未勾选）：{deliv}
Auditor 最近判定：passed={ctx.get('passed_count')} total={ctx.get('total_count')} remaining={remaining}
{unknown_warn}REVIEW.md 全文：
{ctx.get('review') or '(无)'}
已完成文件（绝对路径）：
{chr(10).join(files) if files else '(无)'}
父模块依赖（上游）：{ctx.get('dependencies') or []}
split_depth：{ctx.get('split_depth')}   executor_round：{ctx.get('executor_round')}   model_tier：{ctx.get('model_tier')}

请严格按上面提示词输出合法 JSON（v2 协议：next_block 单块 + remaining_after；只输出 JSON，不要 markdown 代码块标记）。{feedback}"""

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
           "detail": {"split": split_json,
                      "_parse": {"source": "demo", "layer": 1, "repaired": False,
                                 "candidates": 1, "parsed": 1, "truncated": False}},
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
# BUG-20260903-A① 修复：fw-spawn.py 实际在包根 bin/ 目录，原 `$(dirname "$0")/fw-spawn.py`
# 指向 scripts/fw-spawn.py 不存在 → python3 报错被 &>/dev/null 吞掉 → dsh 从未启动 →
# split_output.txt 恒空 → 提取段 exit 3「agent 非零退出(3)」。改为按候选路径探测 + 缺失即清晰报错。
#
# 2026-09-04 小澈复查补强：本脚本是唯一需要**跨目录**找 fw-spawn.py 的角色（split 被收进
# 包内 fw_runner/scripts/，而 fw-spawn.py 与 executor/auditor 还在包外 fw-runner/bin/）。
# 相对路径依赖安装布局，一旦换布局（wheel 进 site-packages）就再次"方法根本不存在"。
# 所以候选顺序按"确定性从高到低"排：显式 env > FW1（autoknit launcher 必带）> 相对包根 > 相对自身。
FW_SPAWN="${FW_SPAWN:-}"
if [ -z "$FW_SPAWN" ]; then
  for cand in "${FW1:+$FW1/fw-runner/bin/fw-spawn.py}" \
              "$(dirname "$0")/../../bin/fw-spawn.py" \
              "$(dirname "$0")/fw-spawn.py"; do
    if [ -n "$cand" ] && [ -f "$cand" ]; then FW_SPAWN="$cand"; break; fi
  done
fi
if [ ! -f "$FW_SPAWN" ]; then
  echo "[fw-split] ✗ 缺 fw-spawn.py：FW_SPAWN 未设，且候选路径全部未命中" >&2
  echo "           FW1=${FW1:-'(未设置)'}" >&2
  echo "           1) \${FW1}/fw-runner/bin/fw-spawn.py" >&2
  echo "           2) $(dirname "$0")/../../bin/fw-spawn.py" >&2
  echo "           3) $(dirname "$0")/fw-spawn.py" >&2
  echo "           修法：经 autoknit 入口调用（它会 export FW1），或直接 export FW_SPAWN=<路径>" >&2
  exit 2
fi
echo "[fw-split] 调 dsh headless 拆分（model=${FW_SPLIT_MODEL}，spawn=${FW_SPAWN}）…"

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

# P1-4（2026-09-04 小澈复查，台账 #6）：原先 &>/dev/null 把 dsh 的全部输出丢进黑洞，
# 且 wait 的退出码也丢了——BUG-20260903-A① 排了整场的真凶就是这个：dsh 从未启动，
# 而现场只显示"未找到拆解 JSON"。改为落盘 split_spawn.log + 捕获退出码 + 失败带上下文。
: > tmp/split_spawn.log
python3 "$FW_SPAWN" -- "$DSH_BIN" --profile headless $DSH_PATCH_ARG "$ROLE_TASK" \
    --out tmp/split_output.txt --timeout "$FW_SPLIT_TIMEOUT" \
    --cwd "$MODULE_DIR" > tmp/split_spawn.log 2>&1 &
SPAWN_PID=$!
SPAWN_RC=0
wait $SPAWN_PID || SPAWN_RC=$?
if [ "$SPAWN_RC" -ne 0 ]; then
  echo "[fw-split] ⚠ spawn 退出码 $SPAWN_RC（dsh 可能没起来/超时），日志 tmp/split_spawn.log 尾部：" >&2
  tail -n 8 tmp/split_spawn.log | sed 's/^/    /' >&2
fi

# ---------- 4. 提取拆解 JSON → 写 outcome ----------
python3 - "$OUTCOME" "$CONTEXT_FILE" <<'PYEOF'
import json, sys
outcome, context_file = sys.argv[1], sys.argv[2]
try:
    text = open("tmp/split_output.txt", encoding="utf-8").read()
except Exception as e:
    print(f"[fw-split] ✗ 读 dsh 输出失败: {e}", file=sys.stderr)
    sys.exit(2)

# ---- 提取层（层②格式修复 / 层④兜底）：单一实现优先走 fw_runner.llmjson ----
# 2026-09-04 小澈复查 P1-5：原先这里有一份与 llmjson.extract_json_objects 逐行等价的
# 内联实现，两份并存且语义已分叉（这边取"最后一个含 action"，那边取"第一个过 schema"）。
# 现在统一由 llmjson 提供，本脚本只决定选取策略；fw_runner 不可导入时才退回内联兜底
# （保留"脚本能脱离包独立跑"的旧能力），并在 meta 里标 source=inline-fallback 留痕。
_llm = None
try:
    from fw_runner import llmjson as _llm
except Exception:
    _llm = None


def _extract(text):
    """返回 (拆解 dict, 解析留痕 meta)。

    meta.layer: 1=直解  2=json_repair 修复  4=兜底/失败    meta.source: llmjson | inline-fallback
    """
    if _llm is not None:
        objs, meta = _llm.extract_json_objects_with_meta(text)
        meta = dict(meta, source="llmjson",
                    layer=2 if meta.get("repaired") else (1 if objs else 4))
        for d in reversed(objs):          # agent 通常在末尾给终稿 → 取最后一个含 action 的
            if isinstance(d.get("action"), str):
                return d, meta
        return (objs[-1], meta) if objs else (None, meta)
    # ---- 内联兜底（fw_runner 不可导入时）----
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith("```")]
    text = "\n".join(lines)
    cands, i = [], 0
    truncated = False
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
            truncated = True
            i = start + 1
            continue
        cands.append(text[start:end + 1])
        i = end + 1
    try:
        import json_repair as _jr
    except ImportError:
        _jr = None
    parsed, repaired = [], False
    for c in cands:
        d = None
        try:
            d = json.loads(c)
        except Exception:
            if _jr is not None:
                try:
                    d = _jr.loads(c)
                    repaired = True
                except Exception:
                    d = None
        if isinstance(d, dict):
            parsed.append(d)
    meta = {"source": "inline-fallback", "candidates": len(cands), "parsed": len(parsed),
            "repaired": repaired, "truncated": truncated,
            "layer": 2 if repaired else (1 if parsed else 4)}
    for d in reversed(parsed):
        if isinstance(d.get("action"), str):
            return d, meta
    return (parsed[-1], meta) if parsed else (None, meta)

ctx = json.load(open(context_file, encoding="utf-8"))
split_json, parse_meta = _extract(text)
if split_json is None:
    print(f"[fw-split] ✗ dsh 输出中未找到拆解 JSON（提取层 meta={parse_meta}，"
          f"实现在 {parse_meta.get('source')}）；详见 tmp/split_output.txt 尾部", file=sys.stderr)
    tail = text.strip().splitlines()[-8:]
    for ln in tail:
        print("  " + ln, file=sys.stderr)
    sys.exit(3)

json.dump({"status": "ok", "verdict": "", "root": "",
           "detail": {"split": split_json, "_parse": parse_meta},
           "reason": f"dsh headless split agent 输出（{ctx.get('mid')}）"},
          open(outcome, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(f"[fw-split] 完成，拆解 JSON → {outcome}（action={split_json.get('action')} "
      f"layer={parse_meta.get('layer')} repaired={parse_meta.get('repaired')}）")

PYEOF
rc=$?
[ "$rc" -eq 0 ] || exit "$rc"
exit 0
