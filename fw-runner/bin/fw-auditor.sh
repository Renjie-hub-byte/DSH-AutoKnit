#!/usr/bin/env bash
# fw-auditor.sh —— framework-v1 真实 auditor 包装器（子进程形态）
# 被 fw-runner 以 --auditor-cmd 调用；cwd = 模块目录。
#
# ⚠️ KV 缓存前缀区冻结（2026-08-28，动顺序前先读）：
#   AUDIT_TASK.md 分层 = 前缀区（角色/数据契约/职责/铁律/判定，heredoc 模板 + 环境清单 + 验收依据）
#   + 动态区（产物/测试清单 + EXEC_TRACE + 交付说明）。同模块 retry 轮次前缀走到验收依据。
#   铁律：每轮变化的 EXEC_TRACE / 交付说明 / 产物清单必须留在尾部，不许插进前缀区。
#
# 职责：读模块产物 → 对照任务书 acceptance 验收 → 写 tmp/auditor-outcome.json
# 输出协议（机器可解析，对齐 runner._valid 三态校验）：
#   verdict: pass | partial | block
#   root:    self | upstream | contract
#   confidence: 0-1
#   passed_count / total_count / remaining_items: 交付物计数（三态都写，partial 必填）
#   blocker/reason: 机器可读文本
#
# 模式（FW_AUDITOR_MODE）：
#   demo   —— 链路联调：有产物即 pass（快速冒烟）
#   dsh    —— 真实审计：dsh headless 对照验收逐条核（默认）
set -uo pipefail

MODULE_DIR="${MODULE_DIR:?}"
TASK_ROOT="${TASK_ROOT:?}"
ROUND="${ROUND:-1}"
EXECUTOR_ID="${EXECUTOR_ID:-E1}"
MODE="${MODE:-speed_first}"
FW_AUDITOR_MODE="${FW_AUDITOR_MODE:-dsh}"
# v1.3（2026-08-27）：final_block 收官轮标记（runner 注入，auditor 验收剩余做全了没）
FW_FINAL_BLOCK="${FW_FINAL_BLOCK:-}"

# 环境预置（2026-08-25）：内部所有 python 调用一律用 fw-env 预置的 venv python（有 yaml/jsonschema/pytest），
# 没有则回退系统 python3——保证角色看到的环境与脚本跑的环境一致（不脑补、不半途崩）
ENV_PY="python3"
if [ -x "$TASK_ROOT/.venv/bin/python" ]; then
  ENV_PY="$TASK_ROOT/.venv/bin/python"
fi

cd "$MODULE_DIR" || exit 2
mkdir -p tmp

# share 层健壮化（2026-09-01）：与 fw-executor.sh 一致，把所有模块 src 加入 PYTHONPATH，
# 使 auditor 跑跨模块集成测试（如 m03 测试 import m02 的包）无需 sys.path hack、不依赖绝对路径。
FW_MOD_SRCS=""
for _d in "$TASK_ROOT"/modules/*/src; do
  [ -d "$_d" ] && FW_MOD_SRCS="${FW_MOD_SRCS}${_d}:"
done
export PYTHONPATH="${FW_MOD_SRCS}${PYTHONPATH:-}"
OUTCOME="tmp/auditor-outcome.json"

echo "[fw-auditor] module=$(basename "$MODULE_DIR") round=$ROUND mode=$FW_AUDITOR_MODE"

# ---------- 1. 收集上下文 ----------
BOOK=""
for f in 任务书-*.yaml; do [ -f "$f" ] && BOOK="$f" && break; done
[ -z "$BOOK" ] && echo "[fw-auditor] ✗ 找不到 任务书-*.yaml" >&2 && exit 2

case "$FW_AUDITOR_MODE" in
  demo)
    # 链路联调：三态判定 + 计数（快速冒烟）
    # total = 任务书 acceptance 条数（无清单时按 1）；纯文本解析，不依赖 yaml 库
    TOTAL_D="$("$ENV_PY" - "$BOOK" <<'PYEOF'
import re, sys
txt = open(sys.argv[1], encoding="utf-8").read()
n = 0
in_acc = False
for ln in txt.splitlines():
    s = ln.strip()
    if re.match(r"^acceptance:", s):
        in_acc = True
        continue
    if in_acc:
        if re.match(r"^[-*]\s+", s):
            n += 1
        elif re.match(r"^[A-Za-z_\u4e00-\u9fff]", s) and ":" in s:
            break
print(n if n else 1)
PYEOF
)"
    ART="$(find src -type f ! -name '.gitkeep' ! -name 'demo-output.txt' -size +0c 2>/dev/null | wc -l | tr -d ' ')"
    [ -z "$ART" ] && ART=0
    DONE_N=0
    if [ -f REVIEW.md ]; then
      # 只数 REVIEW 已做节的真实条目（- x），排除 - [ ] todo 与 - （占位）
      DONE_N="$(grep -E '^- [^[]' REVIEW.md 2>/dev/null | grep -v '占位' | wc -l | tr -d ' ')"
      [ -z "$DONE_N" ] && DONE_N=0
    fi
    PASSED=$((ART + DONE_N))
    [ "$PASSED" -gt "$TOTAL_D" ] && PASSED="$TOTAL_D"
    if [ "$TOTAL_D" -ge 1 ] && [ "$PASSED" -ge "$TOTAL_D" ]; then
      VERD="pass"; ROOTV=""; CONF=0.9; BLK=""
      REASON="demo auditor：交付齐全（${PASSED}/${TOTAL_D}）"
    elif [ "$PASSED" -ge 1 ]; then
      VERD="partial"; ROOTV=""; CONF=0.7
      REASON="demo auditor：部分交付（${PASSED}/${TOTAL_D}）"
      BLK="缺 $((TOTAL_D - PASSED)) 项交付"
    else
      VERD="block"; ROOTV="self"; CONF=0.5
      REASON="demo auditor：无 src/ 产物"; BLK="缺 src/ 实现"
    fi
    "$ENV_PY" - "$OUTCOME" "$VERD" "$ROOTV" "$CONF" "$REASON" "$BLK" "$PASSED" "$TOTAL_D" <<'PYEOF'
import json, sys
outcome, verd, rootv, conf, reason, blk, passed, total = sys.argv[1:9]
# BUG-002a（2026-08-25）：demo 模式基于真实产物文件+REVIEW 条目统计 → 证据等级 L2（内容取证）
json.dump({"status": "ok", "verdict": verd, "root": rootv,
           "confidence": float(conf), "reason": reason, "blocker": blk,
           "passed_count": int(passed), "total_count": int(total),
           "remaining_items": [],
           "evidence_level": "L2",
           "evidence": ["src 产物文件存在且非空", "REVIEW.md 完成条目统计"]},
          open(outcome, "w", encoding="utf-8"))
print(f"[fw-auditor] 判定={verd} 计数={passed}/{total}")
PYEOF
    exit 0
    ;;
  dsh)
    # ---------- 2. 组装审计指令（自包含，瘦身：只给 objective+acceptance，不给整个 yaml/脏交付） ----------
    PROD="$(find src -type f ! -name '.gitkeep' ! -name 'demo-output.txt' -size +0c 2>/dev/null | sed 's|^src/||' | tr '\n' ' ')"
    # 任务书只提取【本块验收】+ 总目标 + 契约 data_shape（瘦身：不给模块级 acceptance 全量）
    OBJ_ACC="$("$ENV_PY" - "$BOOK" <<'PYEOF'
import sys
try:
    import yaml
    doc = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
except Exception as e:
    print(f"(任务书解析失败: {e})")
    raise SystemExit
mods = doc.get("modules") if isinstance(doc, dict) else None
mod = mods[0] if isinstance(mods, list) and mods else (doc.get("task") or {})
task_meta = doc.get("task") or {}
pb = task_meta.get("prediction_baseline") or {}
# v1.3（2026-08-25）：验收项分级——auto=代码层（auditor 判）；manual=GUI/真实场景（框架不判，交人+外部AI）
def _split_acc(acc):
    auto, manual = [], []
    for a in acc or []:
        if isinstance(a, dict):
            text = str(a.get("text") or a.get("name") or "")
            check = str(a.get("check") or "auto").lower()
        else:
            text, check = str(a), "auto"
        (manual if check == "manual" else auto).append(text)
    return auto, manual
lines = []
# 【总】目标与边界（审计参照，防跑偏）
goal = task_meta.get("goal") or ""
if goal:
    lines.append("【总 · 任务总目标】" + str(goal)[:200])
wnh = pb.get("will_not_have") or []
if wnh:
    lines.append("【总 · 明确不做 will_not_have】" + "；".join(str(x) for x in wnh[:4]))
fb = mod.get("first_block") or {}
import os as _os
FINAL_BLOCK = _os.environ.get("FW_FINAL_BLOCK") == "1"
if FINAL_BLOCK:
    # v1.3（2026-08-27）：收官轮——验收模块级完整 acceptance（含 remaining 项），检查剩余是否做全
    acc = mod.get("acceptance") or []
    auto_acc, manual_acc = _split_acc(acc)
    rem = mod.get("remaining_estimate") or {}
    lines.append("【本 · 本轮验收（收官轮 final block：检查剩余部分是否做全）】")
    if rem.get("scope"):
        lines.append("- 剩余范围（本轮必须交付）: " + str(rem.get("scope"))[:200])
    if rem.get("estimate_lines"):
        lines.append(f"- 剩余量级约 {rem['estimate_lines']} 行")
    lines.append("- 下面验收清单 = 剩余项（模块级 acceptance），只核这些 + EXEC_TRACE 本轮增量文件；已验收轮次不重读（2026-09-01 热续作优化）。")
    lines.append("- 验收清单（auto=代码层，你只判这些）：")
    for i, a in enumerate(auto_acc, 1):
        lines.append(f"  {i}. {a}")
    if manual_acc:
        lines.append("【人 · 人工验收项（★ 你不判这些，框架验收不出来）】")
        for i, a in enumerate(manual_acc, 1):
            lines.append(f"  - {a}")
        lines.append("  这些项（GUI 效果/真实场景/体验）归人+外部 AI 验收，你只需在 tmp/audit-result.json 的 human_pending 里原样列出，不进 passed/total 计数。")
elif fb:
    scope = fb.get("scope") or ""
    acc = fb.get("acceptance") or []
    auto_acc, manual_acc = _split_acc(acc)
    rem = mod.get("remaining_estimate") or {}
    lines.append("【本 · 本轮验收（唯一权威，只审计这一块）】")
    if scope:
        lines.append("- scope: " + str(scope).strip())
    lines.append("- 验收清单（auto=代码层，你只判这些）：")
    for i, a in enumerate(auto_acc, 1):
        lines.append(f"  {i}. {a}")
    if manual_acc:
        lines.append("【人 · 人工验收项（★ 你不判这些，框架验收不出来）】")
        for i, a in enumerate(manual_acc, 1):
            lines.append(f"  - {a}")
        lines.append("  这些项（GUI 效果/真实场景/体验）归人+外部 AI 验收，你只需在 tmp/audit-result.json 的 human_pending 里原样列出，不进 passed/total 计数。")
    if rem.get("scope"):
        lines.append("【后 · 剩余范围（⚠️ 不是本轮验收范围）】" + str(rem.get("scope"))[:200])
        if rem.get("estimate_lines"):
            lines.append(f"- 剩余量级约 {rem['estimate_lines']} 行（后续块审计，本轮不管）")
else:
    obj = mod.get("objective")
    acc = mod.get("acceptance") or []
    auto_acc, manual_acc = _split_acc(acc)
    if obj:
        lines.append("【本 · 本轮验收（模块即本轮）】objective: " + str(obj))
    lines.append("【本 · 验收清单（auto=代码层，你只判这些）】")
    for i, a in enumerate(auto_acc, 1):
        lines.append(f"  {i}. {a}")
    if manual_acc:
        lines.append("【人 · 人工验收项（★ 你不判这些，框架验收不出来）】")
        for i, a in enumerate(manual_acc, 1):
            lines.append(f"  - {a}")
        lines.append("  这些项（GUI 效果/真实场景/体验）归人+外部 AI 验收，你只需在 tmp/audit-result.json 的 human_pending 里原样列出，不进 passed/total 计数。")
# 【契约】本模块接口 data_shape（字段对齐核对用）
ifs = mod.get("interfaces") or []
if ifs:
    lines.append("【契约 · 本模块接口 data_shape（字段对齐核对）】")
    for i, x in enumerate(ifs, 1):
        if isinstance(x, dict):
            path = x.get("path", "")
            note = (x.get("note") or "").strip()
            ds = x.get("data_shape")
            lines.append(f"  {i}. {path} — {note}" if path else f"  {i}. {x}")
            if ds:
                lines.append(f"     data_shape: {str(ds)[:400]}")
print("\n".join(lines))
PYEOF
)"
    DELIV=""
    if [ -f 交付说明.md ]; then
      # 交付说明过滤 runner 的"未落档"兜底占位，只留 executor 真实内容
      DELIV="$(grep -v "未落档\|跑者兜底\|runner(兜底" 交付说明.md 2>/dev/null | head -15)"
    fi

    RESULT_FILE="tmp/audit-result.json"
    cat > tmp/AUDIT_TASK.md <<TASKEOF
你是框架派出的独立 auditor（只读验收）。工作区：${MODULE_DIR}（绝对路径）。

【数据契约】（跨模块共享存储/枚举/布局；验收「协议对齐」字段依据；任务根 contracts/data.yaml 为唯一事实源）
$(if [ -f "$TASK_ROOT/contracts/data.yaml" ]; then cat "$TASK_ROOT/contracts/data.yaml"; else echo "- 本任务无跨模块共享数据契约（这是正常的）：以任务书接口 data_shape 为准。"; fi)

【职责】对照验收清单逐条核验 src/ 产物，只判代码层（auto），不判 GUI/人工项。

【铁律】
- 采证层（pytest 实跑结果/越界检查）为客观结果，EXEC_TRACE.md 为动作记录，两者冲突时以采证层为准
- 已有的一律不重复 read/重跑；程序采证结果（pytest/semgrep/codegraph）就在下方【采证】层，直接引用，不要自己重跑
- **抽查不全量（2026-09-01）**：EXEC_TRACE 已给本轮 write/edit 增量文件清单；采证① pytest 已覆盖的验收项直接引用 L1，不必再读源码；需要 L2 取证时只抽查与验收项直接相关的符号/文件，**禁止通读全部 src/test**（那是探索，浪费 token）
- 产物真实性：占位/空壳 → block；协议对齐：contract 声明 vs 实际产物
- 证据等级：L1=测试实跑结果（采证①）L2=读取文件取证 L3=仅静态推断（**无实证的 pass 不成立**）；程序已采好的证据原样引用即可，你不负责采证
- 越界：程序已用 codegraph/find 查过，结果见【采证】层，你不负责查

【判定】先写 tmp/audit-result.json：
{"verdict":"pass|partial|block","root":"self|upstream|contract|","confidence":0.0-1.0,
 "reason":"一句话","passed_count":N,"total_count":N,
 "remaining_items":["未通过项"],"evidence_level":"L1|L2|L3",
 "evidence":["证据"],"human_pending":["人工验收项"]}
写完后回复 JSON_OK。
TASKEOF
    TEST_FILES="$(find test -type f ! -name '.gitkeep' ! -name '.auditor-ignore' -size +0c 2>/dev/null | sed 's|^test/||' | tr '\n' ' ')"
    # ---- 程序预采证（2026-08-31 减负）：auditor 不再亲自跑测试/扫 semgrep/查越界，
    # 这三样由程序预先跑好、结果注入【采证】层，auditor 只对照清单引用放行。
    # 关键：程序采证与 auditor 判定解耦——测试是否全绿是客观事实，不该靠 auditor 的 LLM 自己去跑。
    PY_BIN="$("$ENV_PY" - "$TASK_ROOT/tmp/env-manifest.json" <<'PYEOF'
import json, sys
m = json.load(open(sys.argv[1], encoding="utf-8"))
print(m.get("python") or "")
PYEOF
)"
    EVIDENCE_LAYER=""
    # 程序跑 pytest（客观测试结果）
    if [ -n "$PY_BIN" ] && [ -x "$PY_BIN" ] && [ -d test ]; then
      PTEST_OUT="$("$PY_BIN" -m pytest test/ -q 2>&1 | tail -5 || true)"
      EVIDENCE_LAYER="${EVIDENCE_LAYER}================ 采证① 测试实跑（程序自动执行，结果已定，你不用重跑）================
${PTEST_OUT}

"
    fi
    # 程序跑 semgrep（模式 bug 扫描，可用才跑）
    if command -v semgrep >/dev/null 2>&1 && [ -d src ]; then
      SEMGREP_OUT="$(semgrep scan --config auto --include '*.py' src/ 2>&1 | grep -vE '^\s*$|Downloading|Scanning|Ran |^\d' | tail -8 || true)"
      EVIDENCE_LAYER="${EVIDENCE_LAYER}================ 采证② semgrep 模式扫描（程序自动执行）================
${SEMGREP_OUT:-（无发现）}

"
    fi
    # 程序查越界（产物是否都在本模块目录内）
    OUTSIDE="$("$ENV_PY" - "$MODULE_DIR" <<'PYEOF'
import sys, os
root = os.path.abspath(sys.argv[1])
bad = []
for base in ("src", "test"):
    d = os.path.join(root, base)
    if not os.path.isdir(d):
        continue
    for dirpath, _dirs, files in os.walk(d):
        for fn in files:
            p = os.path.join(dirpath, fn)
            if os.path.islink(p):
                tgt = os.path.realpath(p)
                if not tgt.startswith(root):
                    bad.append(p)
print("；".join(bad) if bad else "（无越界产物）")
PYEOF
)"
    EVIDENCE_LAYER="${EVIDENCE_LAYER}================ 采证③ 产物越界检查（程序自动执行）================
${OUTSIDE}

"
    {
      echo ""
      # 环境清单注入（2026-08-25）：auditor 只能真跑 ✓ 的命令；pytest 可用就必须真跑测试
      if [ -f "$TASK_ROOT/tmp/env-manifest.json" ]; then
        echo "================ 环境清单（fw-env 预置，★ 必须遵守）================"
        "$ENV_PY" - "$TASK_ROOT/tmp/env-manifest.json" <<'PYEOF'
import json, sys
m = json.load(open(sys.argv[1], encoding="utf-8"))
cmds = m.get("commands", {})
avail = " ".join(k for k, v in cmds.items() if v) or "（无）"
unavail = " ".join(k for k, v in cmds.items() if not v) or "（无）"
print(f"- Python 环境: {m.get('python')}（跑测试/命令一律用它，不要用系统 python）")
print(f"- 依赖已装: {', '.join(m.get('deps_ok', []))}")
print(f"- 可用命令: {avail} —— 测试/越界结果已在下方【采证】层由程序跑好，你**无需重跑**，直接引用采证①的 pytest 输出作判定依据")
print(f"- 不可用命令: {unavail} —— 不得假装执行过；对应验收项如实降级（L2 读文件取证或 partial/block）")
PYEOF
        echo ""
      fi
      echo "================ 验收依据（本块验收 + 总目标 + 契约）================"
      echo "$OBJ_ACC"
      echo ""
      # 程序预采证结果注入（pytest/semgrep/越界检查已由程序跑好）
      printf '%s' "$EVIDENCE_LAYER"
      echo "================ 本轮产物（src/ 文件清单）================"
      echo "$PROD"
      echo ""
      echo "================ 本轮测试文件（test/ 清单，跑它们验证）================"
      echo "${TEST_FILES:-（无）}"
      echo ""
      if [ -f tmp/EXEC_TRACE.md ]; then
        echo "================ 客观事实清单（程序采集事件流，唯一事实依据，以此为准）================"
        cat tmp/EXEC_TRACE.md
        echo ""
      fi
      echo "================ 交付说明（executor 自述，仅供参考，非事实依据）================"
      echo "$DELIV"
      echo ""
    } >> tmp/AUDIT_TASK.md

    # 判定解析重试提示（2026-08-31 减负）：上一轮 auditor 判定未被程序解析（parse_failed），
    # 重试时注入明确格式指令，避免再次格式漂移。仅本次生效（用完即删标记）。
    if [ -f tmp/.auditor_parse_retry ]; then
      rm -f tmp/.auditor_parse_retry
      cat >> tmp/AUDIT_TASK.md <<'PARSEEOF'

【⚠️ 重要：上一轮你的判定未被程序解析】
- 程序没读到你的 verdict。请务必严格按【判定】段要求，把判定写入 `tmp/audit-result.json`（JSON 文件），
  不要只写在对话/报告里；verdict 字段必须是 `pass` / `partial` / `block` 三者之一。
- 写完 json 后，最后一行回复 JSON_OK。
PARSEEOF
      echo "[fw-auditor] 检测到判定解析重试标记，已注入格式提示"
    fi

    # ---------- 3. 执行 dsh headless 审计（只读） ----------
    echo "[fw-auditor] 调 dsh headless 审计…"
    DSH_BIN="${DSH_BIN:-$HOME/Library/Application Support/QClaw/npm-global/bin/dsh}"
    # 启动预检（2026-08-30 案例6）：与 fw-executor.sh 同款——缺二进制/缺凭据打人话错误，exit 2
    if [ ! -x "$DSH_BIN" ]; then
      echo "[fw-auditor] ✗ dsh 二进制不可用: ${DSH_BIN}（不存在或不可执行）" >&2
      echo "  修复方式: ① 确认 dsh CLI 已安装（npm install -g dsh 或 QClaw 内置路径）；" >&2
      echo "           ② 装在别处就 export DSH_BIN=/绝对路径/dsh 后重跑" >&2
      exit 2
    fi
    DSH_HOME_DIR="${DSH_HOME:-$HOME/.fw-dsh}"
    if [ ! -f "$DSH_HOME_DIR/settings.yaml" ] && [ ! -f "$DSH_HOME_DIR/.credentials.yaml" ]; then
      echo "[fw-auditor] ✗ DSH_HOME 凭据缺失: ${DSH_HOME_DIR}（settings.yaml 与 .credentials.yaml 都不存在）" >&2
      echo "  修复方式: ① 运行 dsh 登录（dsh login），凭据写入 \$DSH_HOME/.credentials.yaml；" >&2
      echo "           ② fw 默认用独立 HOME ~/.fw-dsh（可 FW_DSH_HOME 覆盖），把凭据文件放进去或软链到 ~/.dsh" >&2
      exit 2
    fi
    ROLE_TASK="$(cat tmp/AUDIT_TASK.md)"
    : > tmp/auditor_output_${ROUND}.txt
    rm -f "$RESULT_FILE"
    FW_AUDITOR_TIMEOUT="${FW_AUDITOR_TIMEOUT:-600}"

    # 可选：指定 auditor 模型 → 生成 patch 覆盖 agent-default-model
    # provider 可配：默认 deepseek-official，可用 FW_AUDITOR_PROVIDER 覆盖
    # reasoningEffort 默认不设：flash 不支持；只有 pro 支持 → 显式设 FW_AUDITOR_REASONING 才加
    DSH_PATCH_ARG=""
    FW_PROVIDER="${FW_AUDITOR_PROVIDER:-deepseek-official}"
    FW_REASONING="${FW_AUDITOR_REASONING:-}"
    REASONING_LINE=""
    [ -n "$FW_REASONING" ] && REASONING_LINE="    reasoningEffort: $FW_REASONING"
    if [ -n "${FW_AUDITOR_MODEL:-}" ]; then
      cat > tmp/model-patch.yml << PATCHEOF
- id: agent-default-model
  config:
    provider: ${FW_PROVIDER}
    model: ${FW_AUDITOR_MODEL}
${REASONING_LINE}
PATCHEOF
      DSH_PATCH_ARG="--patch tmp/model-patch.yml"
      echo "[fw-auditor] 使用模型: ${FW_AUDITOR_MODEL}（provider=${FW_PROVIDER}，仅本 auditor，GUI 不受影响）"
    fi

    FW_SPAWN="$(dirname "$0")/fw-spawn.py"
    # BUG-004 修复（2026-08-25）：auditor 会话 token 统计基准点（spawn 前打点）
    AUDIT_TRACE_MARK="$MODULE_DIR/tmp/.audit-trace-mark"
    touch "$AUDIT_TRACE_MARK" 2>/dev/null || true
    "$ENV_PY" "$FW_SPAWN" -- "$DSH_BIN" --profile headless $DSH_PATCH_ARG "$ROLE_TASK" \
        --out tmp/auditor_output_${ROUND}.txt --timeout "$FW_AUDITOR_TIMEOUT" \
        --cwd "$MODULE_DIR" &>/dev/null &
    SPAWN_PID=$!

    ELAPSED=0
    while [ $ELAPSED -lt $FW_AUDITOR_TIMEOUT ]; do
      # audit-result.json 生成且合法 → kill spawn（内部清理进程组）→ 收工
      if [ -s "$RESULT_FILE" ] 2>/dev/null && "$ENV_PY" -c "import json;json.load(open('$RESULT_FILE'))" 2>/dev/null; then
        kill -TERM $SPAWN_PID 2>/dev/null
        break
      fi
      if ! kill -0 $SPAWN_PID 2>/dev/null; then
        break
      fi
      sleep 2
      ELAPSED=$((ELAPSED + 2))
    done
    wait $SPAWN_PID 2>/dev/null

    # BUG-004 修复（2026-08-25）：auditor 会话 token 统计回填（与 fw-executor.sh 同模式）。
    # BUG-005 修复（2026-08-28）：回填恒 0 —— 未传会话关键字，默认 kw="framework" 过滤掉本次任务会话。
    #   修复：传模块 id（纯 ASCII 前缀，如 m01/m02）缩小范围；since 时间窗（.audit-trace-mark mtime）主导隔离。
    # 基准 = AUDIT_TRACE_MARK 的 mtime（spawn 前打点），只统计本次审计会话。
    export FW_AUDIT_TOKENS_JSON="{}"
    MODULE_ID="$(basename "$MODULE_DIR" | cut -d- -f1)"
    if [ -f "$AUDIT_TRACE_MARK" ]; then
      AUDIT_MARK_MS="$(/usr/bin/stat -f %m "$AUDIT_TRACE_MARK" 2>/dev/null || echo 0)"
      FW_AUDIT_TOKENS_JSON="$("$ENV_PY" "$(cd "$(dirname "$0")/../.." && pwd)/fw-tools/fw-token.py" --json "$MODULE_ID" --since "$AUDIT_MARK_MS" 2>/dev/null || echo '{}')"
    fi
    echo "[fw-auditor] token 统计: $FW_AUDIT_TOKENS_JSON"

    # ---------- 4. 解析判定：优先 json 文件；没有则文本智能解析（双保险） ----------
    "$ENV_PY" - "$OUTCOME" "$RESULT_FILE" "tmp/auditor_output_${ROUND}.txt" <<'PYEOF'
import json, os, re, sys
outcome, resfile, outfile = sys.argv[1], sys.argv[2], sys.argv[3]
# BUG-004 修复（2026-08-25）：auditor 会话 token 回填（与 fw-executor.sh 同模式）
tok = 0
try:
    t = json.loads(os.environ.get("FW_AUDIT_TOKENS_JSON", "{}") or "{}")
    tok = int(t.get("billable_tokens") or 0)
except Exception:
    tok = 0
verdict = rootv = ""
confv = 0.0
reasons = ""
passed = total = 0
remaining = []
used = "json"
# BUG-002a（2026-08-25）：证据等级（L1=命令实跑 L2=内容取证 L3=静态推演）与证据清单
ev_level = ""
ev_list = []
# v1.3（2026-08-25）：人工验收项（check=manual 的验收项，框架验收不出来，交人+外部AI）
human_pending = []


def _text_counts(txt):
    """文本兜底计数（best-effort）：优先 n/m 数字对，再按 剩余/未完成/待办 段收集 remaining 项。"""
    p = t = 0
    rem = []
    low = txt.lower()
    m = re.search(r"(\d+)\s*[/／]\s*(\d+)", low)
    if m:
        p, t = int(m.group(1)), int(m.group(2))
    seg = ""
    for marker in ("剩余", "未完成", "待办", "未实现", "remaining", "todo"):
        i = low.find(marker)
        if i != -1:
            seg = txt[i:i + 500]
            break
    if seg:
        for ln in seg.splitlines():
            s = ln.strip()
            if not s:
                continue
            s = re.sub(r"^[-*•·]\s*", "", s)
            s = re.sub(r"^\d+[.、)）]\s*", "", s)
            s = re.sub(r"^(剩余|未完成|待办|未实现|remaining|todo)\s*[:：]?\s*", "", s, flags=re.I)
            if any(k in s for k in ("✅", "☑", "☑️", "通过", "done", "completed",
                                    "已实现", "已完成", "ok", "confidence")):
                continue
            if ":" in s or "：" in s:
                continue
            if 2 <= len(s) <= 60:
                rem.append(s)
        rem = rem[:20]
    seen, uniq = set(), []
    for x in rem:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return p, t, uniq


def _norm_counts(p, t, rem):
    p = max(0, int(p or 0))
    t = max(0, int(t or 0))
    rem = [str(x).strip() for x in rem if str(x).strip()][:50]
    if t == 0 and p == 0 and rem:
        t = len(rem)
    if t and p > t:
        p = t
    return p, t, rem


# 1) 优先：json 文件（audit-result.json，含三态判定 + 计数）
if resfile and os.path.exists(resfile) and os.path.getsize(resfile) > 0:
    try:
        d = json.load(open(resfile, encoding="utf-8"))
        verdict = str(d.get("verdict") or "")
        rootv = str(d.get("root") or "")
        try:
            confv = float(d.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confv = 0.0
        reasons = str(d.get("reason") or "").strip()
        try:
            passed = int(d.get("passed_count") or 0)
        except (TypeError, ValueError):
            passed = 0
        try:
            total = int(d.get("total_count") or 0)
        except (TypeError, ValueError):
            total = 0
        ri = d.get("remaining_items")
        if isinstance(ri, list):
            remaining = [str(x) for x in ri]
        elif isinstance(ri, str) and ri.strip():
            remaining = [x.strip() for x in re.split(r"[,;、\n]", ri) if x.strip()]
        # BUG-002a（2026-08-25）：证据等级/证据清单（auditor 自证验收依据）
        ev_level = str(d.get("evidence_level") or "").strip().upper()
        evi = d.get("evidence")
        if isinstance(evi, list):
            ev_list = [str(x) for x in evi][:20]
        elif isinstance(evi, str) and evi.strip():
            ev_list = [x.strip() for x in re.split(r"[,;、\n]", evi) if x.strip()][:20]
        # v1.3（2026-08-25）：人工验收项（check=manual，不进判定，交人+外部AI）
        hp = d.get("human_pending")
        if isinstance(hp, list):
            human_pending = [str(x) for x in hp][:30]
        elif isinstance(hp, str) and hp.strip():
            human_pending = [x.strip() for x in re.split(r"[,;、\n]", hp) if x.strip()][:30]
    except Exception:
        verdict = rootv = ""
# 2) 兜底：从文本输出智能解析（auditor 常把判定写进报告不写 json）
if verdict not in ("pass", "partial", "block"):
    used = "text"
    try:
        txt = open(outfile, encoding="utf-8", errors="replace").read()
    except Exception:
        txt = ""
    # BUG-002a（2026-08-25）：文本兜底提取证据等级（auditor 写在报告正文里）
    m = re.search(r"evidence[_ ]?level\s*[:：=]\s*(L[123])", txt, re.I)
    if m:
        ev_level = m.group(1).upper()
    m2 = re.search(r"证据等级\s*[:：=]?\s*(L[123])", txt)
    if m2:
        ev_level = m2.group(1).upper()
    tail = txt.strip()[-1500:]
    low = txt.lower()
    has_pass = (re.search(r"verdict\s*[:=]\s*pass", low)
                or ("验收结论" in txt and ("**pass**" in txt or "**通过**" in txt))
                or "无阻塞项" in txt or "全部通过" in txt or "可验收" in txt
                or re.search(r"判定.{0,6}pass", low) or "通过 4/4" in txt
                or ("4/4" in txt and "通过" in txt))
    frac_m = re.search(r"(\d+)\s*/\s*(\d+)\s*通过", low)
    has_partial = (re.search(r"verdict\s*[:=]\s*partial", low) or "**partial**" in txt
                   or "部分满足" in txt or "部分通过" in txt or "部分完成" in txt
                   or "部分验收" in txt or "部分达成" in txt or "部分不通过" in txt
                   or "部分未通过" in txt or "非全部通过" in txt or "未全部通过" in txt
                   or re.search(r"判定.{0,6}partial", low)
                   or (frac_m and int(frac_m.group(1)) > 0
                       and int(frac_m.group(1)) < int(frac_m.group(2))
                       and not has_pass))
    has_block = ("不通过" in tail or "验收失败" in tail
                 or re.search(r"verdict\s*[:=]\s*block", low)
                 or "全部不通过" in txt or "0/4" in txt)
    if has_pass and not has_partial:
        verdict = "pass"
        m = re.search(r"conf(?:idence)?\s*[:=]\s*([0-9.]+)", low)
        confv = float(m.group(1)) if m else 0.8
        passed, total, remaining = _text_counts(txt)
    elif has_partial:
        verdict = "partial"
        m = re.search(r"conf(?:idence)?\s*[:=]\s*([0-9.]+)", low)
        confv = float(m.group(1)) if m else 0.7
        passed, total, remaining = _text_counts(txt)
        reasons = txt.strip()[-400:].replace("\n", " ")[:380]
    elif has_block:
        verdict = "block"
        m = re.search(r"conf(?:idence)?\s*[:=]\s*([0-9.]+)", low)
        confv = float(m.group(1)) if m else 0.6
        reasons = txt.strip()[-400:].replace("\n", " ")[:380]
# 3) 仍无判定 —— 区分两种本质不同的失败（2026-08-31 减负修复）：
#   - 空输出/超时：auditor 根本没产出判定 → block（agent 能力问题，保持原语义，走升级链）
#   - 有输出但措辞未匹配正则：auditor 审了、代码可能全绿，只是判定文本没被解析器识别
#     → parse_failed（轻量重试，只重让 auditor 出一份结构化判定，不重跑 executor）
if verdict not in ("pass", "partial", "block"):
    if not txt.strip():
        verdict, rootv, confv = "block", "self", 0.3
        used = "timeout"
        reasons = "auditor 无输出/超时（未产出判定）"
    else:
        verdict, rootv, confv = "parse_failed", "", 0.0
        used = "parse_failed"
        reasons = "auditor 判定文本未被解析（见 auditor 输出原文）"
passed, total, remaining = _norm_counts(passed, total, remaining)
# BUG-002a（2026-08-25）：证据等级保守兜底——auditor 未声明验收依据等级 → 一律视为 L3（静态推演）
# 语义：没有实证的 pass 不成立，runner 侧对 L3+pass 强制回人复核
if ev_level not in ("L1", "L2", "L3"):
    ev_level = "L3"
    if verdict == "pass":
        reasons = (reasons + " [⚠️ auditor 未声明证据等级，按 L3 静态推演处理，pass 无效]").strip()
        blocker = reasons
if verdict in ("pass", "partial") and rootv not in ("", "self", "upstream", "contract"):
    rootv = ""
if verdict == "block" and rootv not in ("self", "upstream", "contract"):
    rootv = "self"
confv = max(0.0, min(1.0, confv))
if verdict == "block" and not reasons:
    reasons = "auditor block（详见输出）"
if verdict == "partial" and remaining and not reasons:
    reasons = "partial：剩余交付物 " + "；".join(remaining)
blocker = "" if verdict == "pass" else (reasons or ("partial：剩余 " + "；".join(remaining)))
json.dump({"status": "ok", "verdict": verdict, "root": rootv,
           "confidence": confv, "reason": reasons, "blocker": blocker,
           "tokens": tok,
           "evidence_level": ev_level,
           "evidence": ev_list,
           "human_pending": human_pending,
           "passed_count": passed, "total_count": total,
           "remaining_items": remaining},
          open(outcome, "w", encoding="utf-8"))
print(f"[fw-auditor] 判定={verdict} root={rootv} conf={confv} 计数={passed}/{total} tokens={tok} 证据={ev_level} 人工待验={len(human_pending)}（来源:{used}）")
PYEOF
    exit 0
    ;;
  *)
    echo "[fw-auditor] ✗ 未知模式: $FW_AUDITOR_MODE" >&2
    exit 2
    ;;
esac
