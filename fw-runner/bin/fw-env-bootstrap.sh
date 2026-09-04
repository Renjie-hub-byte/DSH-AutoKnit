#!/usr/bin/env bash
# fw-env-bootstrap.sh —— 任务启动环境预置（Owner 2026-08-25 拍板：角色"能用就用"，
#                         需要的能力在任务开始时配好，不靠角色自己脑补/现场装）
# 用法: fw-env-bootstrap.sh <任务目录>
# 产出（幂等，可重复执行）:
#   $TASK_DIR/.venv                  —— fw 专用 venv（pyyaml/jsonschema/pytest）
#   $TASK_DIR/tmp/env-manifest.json  —— 环境能力清单（命令可用性探测），
#                                      由 fw-executor.sh / fw-auditor.sh 注入角色指令
set -uo pipefail

TASK_DIR="$1"
[ -d "$TASK_DIR" ] || { echo "✗ 任务目录不存在: $TASK_DIR"; exit 1; }
mkdir -p "$TASK_DIR/tmp"
TASK_DIR="$(cd "$TASK_DIR" && pwd)"

VENV="$TASK_DIR/.venv"
MANIFEST="$TASK_DIR/tmp/env-manifest.json"

# ---------- 1. venv + 核心依赖（幂等：python 试运行通过且三依赖可 import → 跳过） ----------
# 坑修复（2026-08-30 案例6）：venv"目录存在"≠健康——死链/损坏的 venv（pyvenv.cfg 指向的
# python 已被删/升级）以前被误判就绪，下游全部静默崩。现在必须试运行通过才算就绪，否则删掉重建。
NEED_INSTALL=1
if [ -x "$VENV/bin/python" ]; then
  if "$VENV/bin/python" -c "pass" >/dev/null 2>&1 \
     && "$VENV/bin/python" -c "import yaml, jsonschema, pytest" >/dev/null 2>&1; then
    NEED_INSTALL=0
  else
    echo "[fw-env] ⚠️ venv 存在但不健康（python 试运行失败或依赖缺失），删除重建: $VENV"
    rm -rf "$VENV"
  fi
elif [ -e "$VENV" ]; then
  echo "[fw-env] ⚠️ $VENV 存在但 python 不可执行（死链/损坏），删除重建"
  rm -rf "$VENV"
fi

if [ "$NEED_INSTALL" = "1" ]; then
  PY_BIN="python3.11"
  command -v python3.11 >/dev/null 2>&1 || PY_BIN="python3"
  # 解释器本身也要试运行：command -v 找到但坏掉的 python 同样会导致静默崩
  if ! "$PY_BIN" -c "pass" >/dev/null 2>&1; then
    echo "✗ python 解释器不可用: $PY_BIN（试运行失败）。请安装 Python 3.11+ 或用 uv: uv python install 3.11"
    exit 1
  fi
  echo "[fw-env] 准备 venv: ${VENV}（python=${PY_BIN}，首次约 10-20s）…"
  "$PY_BIN" -m venv "$VENV" 2>/dev/null || { echo "✗ venv 创建失败（$PY_BIN 不可用）"; exit 1; }
  "$VENV/bin/pip" install -q --disable-pip-version-check pyyaml jsonschema pytest >/dev/null 2>&1
  if ! "$VENV/bin/python" -c "import yaml, jsonschema, pytest" >/dev/null 2>&1; then
    echo "✗ 依赖安装失败（网络？），请手动: $VENV/bin/pip install pyyaml jsonschema pytest"
    exit 1
  fi
  echo "[fw-env] 依赖就绪: pyyaml jsonschema pytest"
else
  echo "[fw-env] venv 已就绪，跳过安装"
fi
VENV_PY="$VENV/bin/python"

# ---------- 1.5 聚合 task.yaml 声明的 python_packages 并安装（2026-09-01 修复） ----------
# 此前 bootstrap 只装核心三件（pyyaml/jsonschema/pytest），task.yaml 里模块声明的
# python_packages（如 zstandard）被完全忽略 → executor 缺依赖被逼自实现解码器（zstd 兔子洞根因）。
# 现在聚合所有模块声明的 python_packages 装一次；pip 幂等（已装会跳过），失败不阻塞 run
# （executor 会按「工具红线」写「已知风险」，不硬装）。
TASK_PKGS="$("$VENV_PY" - "$TASK_DIR/task.yaml" <<'PYEOF'
import sys, yaml
try:
    doc = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    print(""); sys.exit(0)
pkgs = set()
for m in (doc.get("modules") or []):
    for p in ((m.get("environment") or {}).get("python_packages") or []):
        pkgs.add(p)
print(" ".join(sorted(pkgs)))
PYEOF
)"
TASK_PKGS="$(echo "$TASK_PKGS" | xargs)"   # 去首尾空白
if [ -n "$TASK_PKGS" ]; then
  echo "[fw-env] 聚合 task.yaml 声明的 python_packages: $TASK_PKGS"
  # 后台装 + 120s 超时兜底（2026-09-01）：网络慢/断网不能无限挂起 run 启动。
  # set -u 防御（2026-09-02 v4 教训）：planner 写非规范包名（yaml）触发失败分支时 TASK_PKGS 引用 unbound
  "$VENV/bin/pip" install -q --disable-pip-version-check ${TASK_PKGS:-} >/dev/null 2>&1 &
  PIP_PID=$!
  _i=0
  while kill -0 $PIP_PID 2>/dev/null && [ $_i -lt 120 ]; do sleep 1; _i=$((_i+1)); done
  if kill -0 $PIP_PID 2>/dev/null; then
    kill $PIP_PID 2>/dev/null; wait $PIP_PID 2>/dev/null
    echo "[fw-env] ⚠️ 任务依赖安装超时(120s)；executor 将按「工具红线」写「已知风险」，不硬装"
  elif wait $PIP_PID 2>/dev/null; then
    echo "[fw-env] ✓ 任务依赖已装"
  else
    echo "[fw-env] ⚠️ 任务依赖安装失败（可手动: $VENV/bin/pip install ${TASK_PKGS:-}）；executor 将按「工具红线」写「已知风险」，不硬装"
  fi
fi

# ---------- 2. 命令可用性探测（角色只能真跑 ✓ 的命令；✗ 的不得假装执行） ----------
probe() { command -v "$1" >/dev/null 2>&1 && echo true || echo false; }

python_ok=$( "$VENV_PY" -c "print(True)" >/dev/null 2>&1 && echo true || echo false )
pytest_ok=$( [ -x "$VENV/bin/pytest" ] && echo true || echo false )
zstd_ok=$(probe zstd)
git_ok=$(probe git)
node_ok=$(probe node)
npm_ok=$(probe npm)
dsh_ok=$( [ -x "$HOME/Library/Application Support/QClaw/npm-global/bin/dsh" ] && echo true || echo false )
codegraph_ok=$( [ -x "$HOME/.local/bin/codegraph" ] && echo true || echo false )
semgrep_ok=$( [ -x "$HOME/.local/bin/semgrep" ] && echo true || echo false )

# ---------- 3. 生成环境清单（机器可读，供注入角色指令） ----------
"$VENV_PY" - "$MANIFEST" "$TASK_DIR" "$VENV_PY" "$TASK_PKGS" "$python_ok" "$pytest_ok" "$zstd_ok" "$git_ok" \
  "$node_ok" "$npm_ok" "$dsh_ok" "$codegraph_ok" "$semgrep_ok" <<'PYEOF'
import json, sys, datetime

manifest_path, task_dir, venv_py = sys.argv[1], sys.argv[2], sys.argv[3]
task_pkgs = sys.argv[4].split()
flags = sys.argv[5:]
names = ["python", "pytest", "zstd", "git", "node", "npm", "dsh", "codegraph", "semgrep"]
commands = {n: f == "true" for n, f in zip(names, flags)}
deps_ok = ["pyyaml", "jsonschema", "pytest"] + task_pkgs

manifest = {
    "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "task_dir": task_dir,
    "venv": task_dir + "/.venv",
    "python": venv_py,
    "deps_ok": deps_ok,
    "commands": commands,
    "rule": "标注为 true 的命令必须真实执行并引用输出；false 的命令不可用，不得假装执行过，也不得现场安装（缺依赖写进交付说明）",
}
json.dump(manifest, open(manifest_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("[fw-env] 环境清单 -> " + manifest_path)
PYEOF

# ---------- 4. 人类可读摘要 ----------
echo "[fw-env] 命令可用性: python=$python_ok pytest=$pytest_ok zstd=$zstd_ok git=$git_ok dsh=$dsh_ok node=$node_ok npm=$npm_ok codegraph=$codegraph_ok semgrep=$semgrep_ok"
echo "[fw-env] 环境预置完成"
