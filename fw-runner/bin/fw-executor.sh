#!/usr/bin/env bash
# fw-executor.sh —— framework-v1 真实 executor 包装器（子进程形态）
# 被 fw-runner 以 --executor-cmd 调用；cwd = 模块目录。
#
# executor 真身 = dsh --profile headless（咱们自己的 agent：模型路由/工具/沙箱全继承，零 codex 中转）
#
# 职责：
#   1. 读模块任务书 任务书-mXX.yaml + REVIEW.md（交接/反馈） + contract.yaml（契约）
#   2. 拼成一份自包含的"模块执行指令"，交给 dsh headless agent 干活
#   3. agent 在模块目录内产出 → 写 tmp/executor-outcome.json 供 runner 解析
#
# 协议（与 fw-runner drivers.py 对齐）：
#   退出码 0 + tmp/executor-outcome.json 存在 → 本轮成功
#   退出码 13 → 中断（resume）；退出码 != 0 → agent_error（进升级链）
#
# ⚠️ KV 缓存前缀区冻结（2026-08-28，动顺序前先读）：
#   DeepSeek 按请求前缀匹配缓存（前缀一致→整段命中）。EXEC_TASK.md 分层：
#   前缀区（顺序冻结，改动即赔钱）= 角色/铁律/工具/交付/分步/质量（heredoc 模板）+ 环境清单 + 数据契约 +【总】+【前·上游】+【后】+【契约】
#   动态区（每轮变，可自由追加）=【前轮反馈】(review_summary) +【本】本轮任务
#   铁律：①【本】必须永远是最后一段（尾部注意力最强）② 每轮变化的 review_summary 禁止插进前缀区
#         （它会打断缓存前缀）——已固定放在【本】之前 ③ 新增静态段往前放、新增动态段往后放
# 环境变量（runner 注入）：MODULE_DIR TASK_ROOT RUN_ID ROUND ROLE EXECUTOR_ID MODE
# 可配置：FW_EXECUTOR_MODE  demo|dsh（默认 dsh 真身；demo 仅链路联调）
set -uo pipefail

MODULE_DIR="${MODULE_DIR:?}"
TASK_ROOT="${TASK_ROOT:?}"
RUN_ID="${RUN_ID:-run}"
ROUND="${ROUND:-1}"
EXECUTOR_ID="${EXECUTOR_ID:-E1}"
MODE="${MODE:-speed_first}"
FW_EXECUTOR_MODE="${FW_EXECUTOR_MODE:-dsh}"
# v1.3（2026-08-27）：final_block 收官轮标记 + 剩余内容（runner 注入）
FW_FINAL_BLOCK="${FW_FINAL_BLOCK:-}"
FW_REMAINING_SCOPE="${FW_REMAINING_SCOPE:-}"
FW_REMAINING_LINES="${FW_REMAINING_LINES:-}"

# 环境预置（2026-08-25）：内部所有 python 调用一律用 fw-env 预置的 venv python（有 yaml/jsonschema/pytest），
# 没有则回退系统 python3——保证角色看到的环境与脚本跑的环境一致（不脑补、不半途崩）
ENV_PY="python3"
if [ -x "$TASK_ROOT/.venv/bin/python" ]; then
  ENV_PY="$TASK_ROOT/.venv/bin/python"
fi

cd "$MODULE_DIR" || exit 2
mkdir -p tmp

# share 层健壮化（2026-09-01）：把所有模块 src 加入 PYTHONPATH（环境变量，进程级传播到本 executor 的所有 python/bash 子进程）。
# 下游模块无需 sys.path.insert 写死绝对路径——直接 `from <上游包> import <接口>` 即可复用上游已实现能力。
# 未完成的模块 src 目录为空，import 自然失败，不影响；prompt「上游复用纪律」约束只用列出的接口。
FW_MOD_SRCS=""
for _d in "$TASK_ROOT"/modules/*/src; do
  [ -d "$_d" ] && FW_MOD_SRCS="${FW_MOD_SRCS}${_d}:"
done
export PYTHONPATH="${FW_MOD_SRCS}${PYTHONPATH:-}"
OUTCOME="tmp/executor-outcome.json"

echo "[fw-executor] module=$(basename "$MODULE_DIR") exec=$EXECUTOR_ID round=$ROUND mode=$FW_EXECUTOR_MODE"

# ---------- 1. 收集上下文 ----------
BOOK=""
for f in 任务书-*.yaml; do [ -f "$f" ] && BOOK="$f" && break; done
[ -z "$BOOK" ] && echo "[fw-executor] ✗ 找不到 任务书-*.yaml" >&2 && exit 2

REVIEW_TEXT=""
[ -f REVIEW.md ] && REVIEW_TEXT="$(cat REVIEW.md)"
DELIVERY_TEXT=""
[ -f 交付说明.md ] && DELIVERY_TEXT="$(cat 交付说明.md)"

# 上游模块产物可参考（依赖模块）
DEPS=""
if [ -d "$TASK_ROOT/modules" ]; then
  DEPS="$(ls "$TASK_ROOT/modules" 2>/dev/null | tr '\n' ' ')"
fi

case "$FW_EXECUTOR_MODE" in
  demo)
    # 链路联调：写标记产物 + 直接出 outcome
    echo "（演示产物）exec= $EXECUTOR_ID round=$ROUND $(date +%H:%M:%S)" > src/demo-output.txt
    "$ENV_PY" - "$OUTCOME" <<'PYEOF'
import json, sys
json.dump({"status": "ok", "verdict": "", "root": "",
           "substance": True, "tokens": 0,
           "reason": "demo 驱动（链路联调）"}, open(sys.argv[1], "w", encoding="utf-8"))
PYEOF
    echo "[fw-executor] demo 完成 → $OUTCOME"
    exit 0
    ;;
  dsh)
    # ---------- 2. 组装执行指令（自包含） ----------
    cat > tmp/EXEC_TASK.md <<TASKEOF
你是框架派出的模块 executor。工作区：${MODULE_DIR}（绝对路径，所有读写在此目录内）。
纯代码任务，禁止浏览器/GUI/截图；缺依赖如实写进交付说明「已知风险」，不硬装——优先标准库实现。

【铁律】
- 不自定验收标准，不自判通过（auditor 判）
- 只做本模块，不改其他模块，boundaries 是硬墙
- 本轮只做【本】层那一块，remaining 由框架处理，不越界不提前
- **读文件边界（硬墙）**：只允许读本模块目录内文件。本模块 SHARED_CONTEXT.md 的接口摘要、下方 contracts/env 内容已由程序注入，**不必、也不允许**去读任务根目录或任何本模块目录以外的文件——那是别人的实现，读了会引入与你无关的假设并浪费 token；需要的契约/接口都在下方给全了。

【工具】
- 跑代码/测试统一用 \`.venv/bin/python\`（下方环境清单列出的命令可按需使用，测试用 pytest 跑）
- **工具红线（不可越，2026-09-01 通用防线）**：禁止 web_search / 网页抓取（curl/wget 下载） / pip install / npm install / **自实现已有能力的解码器与库（如 zstd）**。解压 zstd 直接 \`zstd -d -c\`；缺依赖只留两条出路：① 写进交付说明「已知风险」② 用下方环境清单已列出的命令。没有的命令 = 如实报告，不硬闯、不造轮。

【交付】
- src/ 实现 + test/ 测试自测跑通
- 交付说明.md：验收对照（每条：测试命令 + 通过/失败）+ 改动文件清单
- REVIEW.md：**先写 `## 已做` 小节**（逐条列本轮完成），再写单行「本轮完成：X｜下轮做：Y｜阻塞：Z」（无则"无"）——续做靠 `## 已做` 小节定位
- 有把握才说完成，不编造

【分步推进】逐条验收，做一条写一条，不一把抓。空壳不算完成。
【代码质量】按本轮验收清单交付：清晰、可测、不过度设计。只为当前块范围留最小可扩展点（如函数默认参数），不为后续块预建抽象、不提前实现剩余部分。

现在开始。直接开工：该给你的全部信息（任务书/REVIEW/契约/上游接口摘要）已拼在下方，不要探索目录、不要找任何上下文文件——没有的就不是该你看到的。
TASKEOF
    {
      echo ""
      # 环境清单注入（2026-08-25）：fw-env 预置的可用能力，角色只能真跑 ✓ 的命令
      if [ -f "$TASK_ROOT/tmp/env-manifest.json" ]; then
        echo "================ 环境清单（fw-env 预置，★ 必须遵守）================"
        "$ENV_PY" - "$TASK_ROOT/tmp/env-manifest.json" <<'PYEOF'
import json, sys
m = json.load(open(sys.argv[1], encoding="utf-8"))
cmds = m.get("commands", {})
avail = " ".join(k for k, v in cmds.items() if v) or "（无）"
unavail = " ".join(k for k, v in cmds.items() if not v) or "（无）"
print(f"- Python 环境: {m.get('python')}（跑代码/测试一律用它，不要用系统 python，不要自建 venv）")
print(f"- 依赖已装: {', '.join(m.get('deps_ok', []))}")
print(f"- 可用命令: {avail}")
print(f"- 不可用命令: {unavail} —— 不得假装执行过，不得现场安装；实在需要就写进交付说明「已知风险」")
PYEOF
        echo ""
      fi
      # 数据契约注入（2026-08-28 可选增强）：任务根 contracts/data.yaml 存在才注入；
      # 内容由 scaffold 程序生成（单一事实源），executor 不需要也不允许自行读上游文件
      if [ -f "$TASK_ROOT/contracts/data.yaml" ]; then
        echo "================ 数据契约（跨模块共享存储/枚举/布局，★全模块必须对齐，禁止自定义表名/路径/格式）================"
        cat "$TASK_ROOT/contracts/data.yaml"
        echo ""
      else
        echo "================ 数据契约 ================"
        echo "- 本任务无跨模块共享数据契约（这是正常的）：跨模块数据对齐以任务书【契约】层接口 data_shape 为准；本模块自己的存储按任务书自由定义。"
        echo ""
      fi
      echo "================ 本轮任务（信息分层：总 / 前 / 后 / 契约 / 本）================"
      "$ENV_PY" - "$BOOK" "$TASK_ROOT" <<'PYEOF'
import sys, re, os
try:
    import yaml
except Exception:
    print("(yaml 不可用，回退原文)")
    print(open(sys.argv[1], encoding="utf-8").read())
    raise SystemExit
doc = yaml.safe_load(open(sys.argv[1], encoding="utf-8")) or {}
mods = doc.get("modules")
mod = mods[0] if isinstance(mods, list) and mods else {}
task_meta = doc.get("task") or {}
pb = task_meta.get("prediction_baseline") or {}

def fmt_list(items):
    out = []
    for i, x in enumerate(items or [], 1):
        if isinstance(x, dict):
            path = x.get("path", "")
            methods = ",".join(x.get("method") or [])
            note = (x.get("note") or "").strip()
            ds = x.get("data_shape")
            line = f"  {i}. {path} ({methods}) — {note}" if path else f"  {i}. {x}"
            if ds:
                line += f"\n     data_shape: {str(ds)[:300]}"
            out.append(line)
        else:
            out.append(f"  {i}. {x}")
    return "\n".join(out)

goal = task_meta.get("goal") or ((mod.get("objective") or "").split("。")[0])
wnh = pb.get("will_not_have") or []
fb = mod.get("first_block") or {}
rem = mod.get("remaining_estimate") or {}

FINAL_BLOCK = os.environ.get("FW_FINAL_BLOCK") == "1"
if FINAL_BLOCK:
    # v1.3（2026-08-27）：收官轮——本轮任务 = remaining_estimate 描述的剩余部分（runner 注入）
    this_scope = os.environ.get("FW_REMAINING_SCOPE") or mod.get("objective", "")
    this_acc = mod.get("acceptance") or []   # 模块级完整验收（含 remaining 项）
    this_lines = os.environ.get("FW_REMAINING_LINES") or ""
    is_final = True
elif fb:
    this_scope = fb.get("scope") or ""
    this_acc = fb.get("acceptance") or []
    this_lines = fb.get("estimate_lines") or ""
    is_final = False
else:
    this_scope = mod.get("objective", "")
    this_acc = mod.get("acceptance") or []
    this_lines = ""
    is_final = True   # 无 first_block = 模块即本轮，全量做完

review_summary = ""
try:
    if os.path.exists("REVIEW.md"):
        txt = open("REVIEW.md", encoding="utf-8").read()
        m = re.search(r"##\s*(已做|done)[^\n]*\n(.*?)(?=\n##|\Z)", txt, re.S | re.I)
        if m:
            lines = [l.strip().lstrip("- ").strip() for l in m.group(2).splitlines() if l.strip()]
            review_summary = "；".join(lines[:5])[:400]
except Exception:
    pass

print(f"\n【总 · 任务全貌】")
print(f"- 总目标: {goal}")
if wnh:
    print(f"- 明确不做 will_not_have: {'；'.join(str(x) for x in wnh[:4])}")
print("")
print("【前 · 上游】")
deps = mod.get("dependencies")
print(f"- 上游依赖模块: {deps or '无'}")
task_root = sys.argv[2] if len(sys.argv) > 2 else ""
sc_path = "SHARED_CONTEXT.md"
if os.path.exists(sc_path):
    try:
        sc_text = open(sc_path, encoding="utf-8").read().strip()
        if len(sc_text) > 6000:
            sc_text = sc_text[:6000] + "\n…（已截断，接口契约见下方【契约】层）"
        print("- 上游接口摘要（程序已注入，直接使用，不要再找文件）：")
        print("  " + sc_text.replace("\n", "\n  "))
    except Exception:
        print("- 上游接口摘要：读取失败（忽略，接口契约在下方【契约】层）")
else:
    print("- 本模块无 SHARED_CONTEXT.md（上游拆分上下文）：这是正常的——你是首个执行单元或顶层模块，")
    print("  没有需要对接的拆分上下文，也无需查找任何上下文文件。接口契约在下方【契约】层已给全。")
# UCD（2026-09-01）：注入已 done 上游模块的 UPSTREAM.md（程序生成的已实现能力摘要，0 token）。
# 下游直接复用上游已实现能力，禁止重写；读边界不含上游模块目录——摘要已由程序拼进来，不需要去找。
if deps and task_root:
    # 2026-09-01 防联想纪律：只允许用「下方列出的接口」，未列出的视为不存在，禁止自行探索上游/其它模块目录。
    # 实测教训：给「上游有什么」而没给「怎么用」，executor 会自己 sys.path hack + 翻上游源码（m03 贵 16% 根因）。
    print("- **上游复用纪律**：只能使用下方列出的上游接口；未列出的能力视为不存在，"
          "禁止自行探索上游/其它模块目录（读了=污染+浪费 token）。接口签名/import 方式看摘要，不要读上游源码。")
    import glob as _glob
    for dep in (deps if isinstance(deps, list) else [deps]):
        dep_dir = None
        for d in _glob.glob(os.path.join(task_root, "modules", f"{dep}-*")):
            if os.path.isdir(d):
                dep_dir = d
                break
        if dep_dir:
            up_path = os.path.join(dep_dir, "UPSTREAM.md")
            if os.path.exists(up_path):
                try:
                    up_text = open(up_path, encoding="utf-8").read().strip()
                    if len(up_text) > 4000:
                        up_text = up_text[:4000] + "\n…（截断）"
                    print(f"- 上游 {dep} 已实现能力摘要（程序注入，0 token；【直接复用，禁止重写】）：")
                    print("  " + up_text.replace("\n", "\n  "))
                except Exception:
                    print(f"- 上游 {dep} 能力摘要读取失败（忽略，接口契约在下方【契约】层）")
            else:
                print(f"- 上游 {dep} 尚无能力摘要（UPSTREAM.md 未生成：上游未完成或无需暴露可复用件），无需查找。")
print("【只对接，不重做】：不重写/不修改上游已完成产物；不复制粘贴与本次交付无关的代码。")
print("")
if not is_final:
    rem_scope = rem.get("scope") or ""
    rem_lines = rem.get("estimate_lines") or ""
    print("【后 · 之后的块（⚠️ 不要做）】")
    print(f"- 剩余范围: {rem_scope or '（后续块）'}")
    if rem_lines:
        print(f"- 剩余量级: 约 {rem_lines} 行")
    print("- 剩余部分由框架 split 拆成后续块、派给下一个 executor 做。本轮不做、不提前实现、不碰。")
    print("")
print("【契约 · 本模块接口（实现须对齐 data_shape）】")
ifs = mod.get("interfaces") or []
print(fmt_list(ifs) if ifs else "(无)")
bs = mod.get("boundaries") or []
if bs:
    print(f"\n【边界 boundaries】")
    print(fmt_list(bs))
print("")
print("【前轮反馈 · 上轮已做（续做定位，仅供回顾）】")
print(f"- {review_summary or '（无/首轮，这是正常的）'}")
print("")
print("【本 · 本轮唯一任务 ⭐（最重要，最后执行指令）】")
if FINAL_BLOCK:
    print(f"- 这是收官轮（final block）：前一块已验收通过，本轮【把剩余部分做完】（约 {this_lines} 行）：")
elif fb:
    print(f"- 这是首发块（估计 {this_lines} 行），本轮【只做】这一块：")
else:
    print("- 本模块即本轮（无首发块拆分），全部做完：")
print(f"- scope: {this_scope}")
print("- 本轮验收清单（唯一权威，对照它自测/交付）：")
print(fmt_list(this_acc) if this_acc else "  (无独立清单，按 scope 完成)")
PYEOF
    } >> tmp/EXEC_TASK.md

    # ---------- 3. 执行（dsh headless = 咱们的 executor 真身） ----------
    echo "[fw-executor] 调 dsh headless 干活（exec=${EXECUTOR_ID} round=${ROUND}）…"
    DSH_BIN="${DSH_BIN:-$HOME/Library/Application Support/QClaw/npm-global/bin/dsh}"
    # 启动预检（2026-08-30 案例6）：dsh 起不来时以前只有 2 行 exec 失败，不指明缺什么。
    # 现在缺二进制/缺凭据直接打人话错误，exit 2（对齐协议：!=0 → agent_error 进升级链）。
    if [ ! -x "$DSH_BIN" ]; then
      echo "[fw-executor] ✗ dsh 二进制不可用: ${DSH_BIN}（不存在或不可执行）" >&2
      echo "  修复方式: ① 确认 dsh CLI 已安装（npm install -g dsh 或 QClaw 内置路径）；" >&2
      echo "           ② 装在别处就 export DSH_BIN=/绝对路径/dsh 后重跑" >&2
      exit 2
    fi
    DSH_HOME_DIR="${DSH_HOME:-$HOME/.fw-dsh}"
    if [ ! -f "$DSH_HOME_DIR/settings.yaml" ] && [ ! -f "$DSH_HOME_DIR/.credentials.yaml" ]; then
      echo "[fw-executor] ✗ DSH_HOME 凭据缺失: ${DSH_HOME_DIR}（settings.yaml 与 .credentials.yaml 都不存在）" >&2
      echo "  修复方式: ① 运行 dsh 登录（dsh login），凭据写入 \$DSH_HOME/.credentials.yaml；" >&2
      echo "           ② fw 默认用独立 HOME ~/.fw-dsh（可 FW_DSH_HOME 覆盖），把凭据文件放进去或软链到 ~/.dsh" >&2
      exit 2
    fi
    ROLE_TASK="$(cat tmp/EXEC_TASK.md)"
    : > tmp/executor_output.txt
    FW_EXECUTOR_TIMEOUT="${FW_EXECUTOR_TIMEOUT:-1800}"

    # 可选：指定 executor 模型（低成本档如 doubao-seed-2.0-mini）→ 生成 patch 覆盖 agent-default-model
    # provider 可配：默认 deepseek-official（官方 API），可用 FW_EXECUTOR_PROVIDER 覆盖（如 volcengine-agent-plan）
    # reasoningEffort 默认不设：flash 不支持 reasoningEffort（报 UNSUPPORTED）；只有 pro 才支持 → 显式设 FW_EXECUTOR_REASONING 才加
    DSH_PATCH_ARG=""
    FW_PROVIDER="${FW_EXECUTOR_PROVIDER:-deepseek-official}"
    FW_REASONING="${FW_EXECUTOR_REASONING:-}"
    REASONING_LINE=""
    [ -n "$FW_REASONING" ] && REASONING_LINE="    reasoningEffort: $FW_REASONING"
    if [ -n "${FW_EXECUTOR_MODEL:-}" ]; then
      cat > tmp/model-patch.yml << PATCHEOF
- id: agent-default-model
  config:
    provider: ${FW_PROVIDER}
    model: ${FW_EXECUTOR_MODEL}
${REASONING_LINE}
PATCHEOF
      DSH_PATCH_ARG="--patch tmp/model-patch.yml"
      echo "[fw-executor] 使用模型: ${FW_EXECUTOR_MODEL}（provider=${FW_PROVIDER}，仅本 executor，GUI 不受影响）"
    fi

    FW_SPAWN="$(dirname "$0")/fw-spawn.py"
    # 会话轨迹基线标记（轨迹证据：spawn 后新生会话 = 本轮 executor）
    TRACE_MARK="$MODULE_DIR/tmp/.trace-mark"
    touch "$TRACE_MARK" 2>/dev/null || true
    "$ENV_PY" "$FW_SPAWN" -- "$DSH_BIN" --profile headless $DSH_PATCH_ARG "$ROLE_TASK" \
        --out tmp/executor_output.txt --timeout "$FW_EXECUTOR_TIMEOUT" \
        --cwd "$MODULE_DIR" &>/dev/null &
    SPAWN_PID=$!

    # 等 dsh 自然完成（它会写代码 + 交付说明 + REVIEW，然后输出总结退出）。
    # ⚠️ 不再"发现产物就 kill"——那会把刚写第一个文件的 dsh 杀掉，导致交付空壳。
    # fw-spawn 内部有 timeout 兜底（到时 kill 进程组）。
    wait $SPAWN_PID 2>/dev/null

    # ---------- 2.5 客观事实采集（程序采集，0 token）----------
    # 解压 executor 本轮会话事件流（tool/call + tool/result 按 callId 配对）→ tmp/EXEC_TRACE.md，
    # 作为 auditor 的唯一事实依据（替代 executor 自述；auditor 只对照判定，不自己 read 全量）。
    TRACE_PY="$(dirname "$0")/fw-trace.py"
    if [ -f "$TRACE_PY" ]; then
      "$ENV_PY" "$TRACE_PY" --mark "$TRACE_MARK" --out tmp/EXEC_TRACE.md \
          --cwd "$MODULE_DIR" --max-bytes 3000
    else
      echo "[fw-executor] ✗ 缺 fw-trace.py，跳过客观事实采集" >&2
    fi
    # 旧版内嵌采集已迁移到 fw-trace.py（下方 heredoc 用 : 置为无操作，保留以减小 diff）
    : <<'PYEOF'
import os, pathlib, re, subprocess, sys
mark = sys.argv[1]
sess_dir = os.path.expanduser("~/.fw-dsh/sessions")
if not os.path.isdir(sess_dir):
    print("[fw-executor] EXEC_TRACE: 无会话目录，跳过"); sys.exit(0)
mark_t = os.path.getmtime(mark)
new = []
for root, _dirs, files in os.walk(sess_dir):
    for f in files:
        if f.startswith("session") and f.endswith(".jsonl.zstd"):
            p = os.path.join(root, f)
            try:
                if os.path.getmtime(p) >= mark_t:
                    new.append(p)
            except OSError:
                pass
if not new:
    print("[fw-executor] EXEC_TRACE: 未发现本轮新会话，跳过")
    sys.exit(0)
lines = ["# EXEC_TRACE.md —— executor 实际动作（程序采集，非 executor 自述）", ""]
from collections import Counter
for p in sorted(new):
    raw = p + ".jsonl"
    try:
        subprocess.run(["zstd", "-d", "-f", p, "-o", raw],
                       capture_output=True, timeout=60)
    except Exception as e:
        lines.append(f"- 会话 {os.path.basename(p)} 解压失败: {e}")
        continue
    calls = []
    try:
        with open(raw, encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                if '"tool/call"' not in ln:
                    continue
                nm = re.search(r'"name"\s*:\s*"([^"]+)"', ln)
                if not nm:
                    continue
                # 动作摘录：data.arguments 是 JSON 字符串（{"command": ...} / {"path": ...}）
                brief = ""
                m2 = re.search(r'"arguments"\s*:\s*"(.{0,240})', ln)
                if m2:
                    arg = m2.group(1).replace('\\"', '"').replace('\\\\', '\\')
                    brief = arg[:160]
                calls.append((nm.group(1), brief))
    except Exception as e:
        lines.append(f"- 会话 {os.path.basename(p)} 解析失败: {e}")
        continue
    cnt = Counter(n for n, _ in calls)
    lines.append(f"## 会话 {os.path.basename(p)}（调用 {len(calls)} 次）")
    lines.append("| 工具 | 次数 |")
    lines.append("|---|---|")
    for n, c in cnt.most_common():
        lines.append(f"| {n} | {c} |")
    lines.append("")
    lines.append("### write/edit/bash 关键动作摘录（最多 15 条）")
    seen, shown = set(), 0
    for name, brief in calls:
        if name in ("bash", "write", "edit", "str_replace_editor") and brief and brief not in seen:
            seen.add(brief)
            lines.append(f"- `{name}` ...{brief}")
            shown += 1
            if shown >= 15:
                break
    lines.append("")
    try:
        os.unlink(raw)
    except OSError:
        pass
text = "\n".join(lines)[:6000]
pathlib.Path("tmp/EXEC_TRACE.md").write_text(text + "\n", encoding="utf-8")
print(f"[fw-executor] EXEC_TRACE 已生成（{len(new)} 个会话，摘要 {len(text)} 字符）")
PYEOF

    # 判断本轮是否有实质产物（dsh 自然完成后检查 src）
    HAS_PRODUCT=""
    P="$(find src -type f ! -name '.gitkeep' ! -name 'demo-output.txt' ! -path '*/__pycache__/*' -size +0c 2>/dev/null | head -1)"
    if [ -n "$P" ]; then
      HAS_PRODUCT="1"
    fi

    if [ -z "$HAS_PRODUCT" ]; then
      echo "[fw-executor] ✗ dsh 未产出实质产物，日志尾见下" >&2
      tail -8 tmp/executor_output.txt >&2
      exit 124
    fi

    echo "[fw-executor] ✓ 实质产物 ${P}，dsh 自然完成收工"

    # UCD（2026-09-01）：交付后程序生成本模块 UPSTREAM.md（AST 提取公开接口，0 token），
    # 供下游模块 executor 启动时注入【前·上游】层复用；生成失败不阻塞（静默跳过）。
    FW_UPSTREAM="$(cd "$(dirname "$0")/../.." && pwd)/fw-tools/fw-upstream.py"
    if [ -f "$FW_UPSTREAM" ]; then
      "$ENV_PY" "$FW_UPSTREAM" "$MODULE_DIR" --max-lines 60 >/dev/null 2>&1 || \
        echo "[fw-executor] ⚠️ UPSTREAM.md 生成失败（忽略）"
    fi


    # ---------- 4. 产出判断 + 写 outcome ----------
    SUBS="false"
    if [ -n "$(find src -type f ! -name '.gitkeep' ! -name 'demo-output.txt' ! -path '*/__pycache__/*' -size +0c 2>/dev/null | head -1)" ]; then
      SUBS="true"
    fi

    # 兜底：executor 若没写交付说明（模板占位残留），包装器自动生成一份
    if [ -f 交付说明.md ] && grep -q "（占位）" 交付说明.md 2>/dev/null; then
      PROD="$(find src -type f ! -name '.gitkeep' ! -path '*/__pycache__/*' -size +0c 2>/dev/null | sed 's|^src/||' | tr '\n' ' ')"
      {
        echo "# 交付说明 —— $(basename "$MODULE_DIR")（自动生成）"
        echo ""
        echo "## 改动内容"
        echo "- executor=$EXECUTOR_ID round=$ROUND 产出: $PROD"
        echo ""
        echo "## 测试结果"
        echo "- 未由 executor 填写；请 auditor/验收侧按 src 实际产物核验"
        echo ""
        echo "## 已知风险"
        echo "- executor 未填写交付文档（headless 会话收工后自动补录）"
      } > 交付说明.md
      echo "[fw-executor] ⚠️ 交付说明为占位 → 已自动补录"
    fi

    # REVIEW.md「已做」兜底
    if [ -f REVIEW.md ] && ! grep -qE "已做|done|完成" REVIEW.md 2>/dev/null; then
      {
        echo ""
        echo "## 已做（executor $EXECUTOR_ID 第 $ROUND 轮，自动补录）"
        echo "- 产出: $(find src -type f ! -name '.gitkeep' ! -path '*/__pycache__/*' -size +0c 2>/dev/null | sed 's|^src/||' | tr '\n' ' ')"
      } >> REVIEW.md
    fi

    # BUG-004 修复（2026-08-25）：从 dsh 会话文件读真实 token 回填（fw-token.py --json --since）。
    # BUG-005 修复（2026-08-28）：回填恒 0 —— 未传会话关键字，默认 kw="framework" 过滤掉本次任务会话。
    #   修复：传模块 id（纯 ASCII 前缀，如 m01/m02）缩小范围；since 时间窗（.trace-mark mtime）是主导隔离，
    #   把历史任务与其它模块的会话排除。两者结合才能精确归集本次执行轮次的真实 token。
    # 基准 = .trace-mark 的 mtime（spawn 前打点），只统计本次会话。
    export FW_TOKENS_JSON="{}"
    MODULE_ID="$(basename "$MODULE_DIR" | cut -d- -f1)"
    if [ -f "$TRACE_MARK" ]; then
      MARK_MS="$(/usr/bin/stat -f %m "$TRACE_MARK" 2>/dev/null || echo 0)"
      FW_TOKENS_JSON="$("$ENV_PY" "$(cd "$(dirname "$0")/../.." && pwd)/fw-tools/fw-token.py" --json "$MODULE_ID" --since "$MARK_MS" 2>/dev/null || echo '{}')"
    fi
    echo "[fw-executor] token 统计: $FW_TOKENS_JSON"

    "$ENV_PY" - "$OUTCOME" "$SUBS" <<'PYEOF'
import json, os, sys
outcome, subs = sys.argv[1], sys.argv[2]
tok = 0
try:
    t = json.loads(os.environ.get("FW_TOKENS_JSON", "{}") or "{}")
    tok = int(t.get("billable_tokens") or 0)
except Exception:
    tok = 0
# 2026-08-25 重构：remaining 不再由 executor 自报——remaining 不是它的活（它只做 first_block），
# 是 planner 拆模块时定的量，runner 出口判定直接从任务书 remaining_estimate 读。
# 这里 remaining_lines 恒为 null，仅保留字段兼容；executor 只需如实报"做完了/没做完"。
rem = None
json.dump({
  "status": "ok", "verdict": "", "root": "",
  "substance": subs == "true", "tokens": tok,
  "remaining_lines": rem,
  "reason": "dsh headless executor 完成（详见交付说明.md）",
}, open(outcome, "w", encoding="utf-8"))
print(f"[fw-executor] 完成，substance={subs} tokens={tok}")
PYEOF
    exit 0
    ;;
  *)
    echo "[fw-executor] ✗ 未知模式: $FW_EXECUTOR_MODE" >&2
    exit 2
    ;;
esac