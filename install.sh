#!/usr/bin/env bash
# AutoKnit 一键安装脚本：clone 下来 → 运行本脚本 → 直接 `autoknit` 可用
#
# 用法:
#   bash install.sh                          # 默认安装到 ~/.autoknit
#   AUTOKNIT_HOME=/opt/autoknit bash install.sh
#   BIN_DIR=/path/to/bin bash install.sh     # 覆盖 launcher 放置目录（默认 ~/.local/bin）
#
# 幂等：重复运行安全（rsync 增量同步 + venv 健康自检 + launcher 覆盖重写）。
# 依赖假设：用户已自行安装并登录 dsh CLI（本脚本不负责装 dsh）。
#   - dsh 二进制默认探测路径：~/Library/Application Support/QClaw/npm-global/bin/dsh
#   - 若装在别处，运行时用 export DSH_BIN=/绝对路径/dsh 覆盖即可。
set -euo pipefail

# ---- 可覆盖的安装参数 ----
AUTOKNIT_HOME="${AUTOKNIT_HOME:-$HOME/.autoknit}"   # 安装根目录
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"              # launcher 放置目录
PY_BIN="${PY_BIN:-}"                                 # 留空则自动探测 python3.11 → python3

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ ! -f "$SRC_DIR/fw-tools/autoknit" ]; then
  echo "✗ 找不到 fw-tools/autoknit。请在 framework-v1 根目录运行本脚本。" >&2
  exit 1
fi

FW_HOME="$AUTOKNIT_HOME/framework-v1"   # 拷贝后的框架根（launcher 里的 FW1 指到这里）
VENV="$AUTOKNIT_HOME/venv"

echo "════════════════════════════════════════════"
echo "  AutoKnit 安装"
echo "  源码      : $SRC_DIR"
echo "  安装根    : $AUTOKNIT_HOME"
echo "  框架目录  : $FW_HOME"
echo "  launcher  : $BIN_DIR/autoknit"
echo "════════════════════════════════════════════"

mkdir -p "$AUTOKNIT_HOME"

# ---- 1. 复制 framework-v1 到安装目录 ----
# 排除：任务/实验/对比记录目录、根级历史 .md 文档、.git/.venv/__pycache__/node_modules/build/*.egg-info。
# 注意：子目录里的 prompts/*.md 是运行期必需产物，不能排除，故 *.md 用 /*.md 锚定到根级。
echo "── 1. 同步源码（幂等，排除历史文档/缓存/构建产物）──"
if command -v rsync >/dev/null 2>&1; then
  rsync -a \
    --exclude '/.git/' \
    --exclude '/.venv/' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude '.pytest_cache/' \
    --exclude '.codegraph/' \
    --exclude '.harness/' \
    --exclude '.DS_Store' \
    --exclude 'build/' \
    --exclude '*.egg-info/' \
    --exclude 'node_modules/' \
    --exclude '/任务-*/' \
    --exclude '/实验-*/' \
    --exclude '/对比记录-*/' \
    --exclude '/*.md' \
    "$SRC_DIR/" "$FW_HOME/"
else
  echo "⚠ 未找到 rsync，改用 cp + 清理（等价排除）"
  rm -rf "$FW_HOME"; mkdir -p "$FW_HOME"
  cp -R "$SRC_DIR"/. "$FW_HOME/"
  cd "$FW_HOME"
  rm -rf .git .venv .pytest_cache .codegraph .harness
  rm -rf 任务-* 实验-* 对比记录-*
  find . -name '__pycache__' -type d -prune -exec rm -rf {} +
  find . -name 'build'          -type d -prune -exec rm -rf {} +
  find . -name 'node_modules'   -type d -prune -exec rm -rf {} +
  find . -name '*.egg-info'     -type d -prune -exec rm -rf {} +
  find . -type f -name '*.pyc'  -delete
  find . -maxdepth 1 -type f -name '*.md' -delete
  cd - >/dev/null
fi

# ---- 2. 独立 venv + 依赖 ----
echo "── 2. 准备 venv 并安装依赖 ──"
if [ ! -x "$VENV/bin/python" ] || ! "$VENV/bin/python" -c 'import sys; assert sys.version_info >= (3,11)' >/dev/null 2>&1; then
  rm -rf "$VENV"
  if [ -n "$PY_BIN" ]; then
    CPY="$PY_BIN"
  elif command -v python3.11 >/dev/null 2>&1; then
    CPY="python3.11"
  elif command -v python3 >/dev/null 2>&1; then
    CPY="python3"
  else
    echo "✗ 需要 Python 3.11+（未找到 python3.11 / python3，可用 PY_BIN 指定）。" >&2
    exit 1
  fi
  echo "   创建 venv: ${VENV}（${CPY}）"
  "$CPY" -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install -q --disable-pip-version-check --upgrade pip
"$VENV/bin/python" -m pip install -q --disable-pip-version-check pyyaml jsonschema
# 注意安装顺序与 --no-deps：fw-runner 的 pyproject 把 fw-protocol/fw-scaffold 声明为
# 路径依赖，若让 pip 递归解析会把这两个包装成【非 editable 拷贝】，丢掉 schema/ 数据目录
# （fw_protocol.schema.load_schema 按包相对路径找 task-schema.json 会崩）。
# 故先 editable 装 fw-protocol/fw-scaffold，再 --no-deps editable 装 fw-runner（复用已装的兄弟包）。
for pkg in fw-protocol fw-scaffold; do
  echo "   pip install -e $pkg"
  "$VENV/bin/python" -m pip install -q --disable-pip-version-check -e "$FW_HOME/$pkg"
done
echo "   pip install -e fw-runner (--no-deps)"
"$VENV/bin/python" -m pip install -q --disable-pip-version-check --no-deps -e "$FW_HOME/fw-runner"
for pkg in fw-api fw-planonly fw-budget fw-integrate; do
  echo "   pip install -e $pkg"
  "$VENV/bin/python" -m pip install -q --disable-pip-version-check -e "$FW_HOME/$pkg"
done

# ---- 3. 写 launcher（幂等覆盖）----
echo "── 3. 生成 launcher ──"
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/autoknit" <<LAUNCHER_EOF
#!/usr/bin/env bash
# AutoKnit launcher —— 由 install.sh 生成，勿手改；重跑 install.sh 会覆盖。
# 依赖假设：你已自行安装并登录 dsh CLI（本层不负责装 dsh）。
AUTOKNIT_HOME="$AUTOKNIT_HOME"
export FW1="\$AUTOKNIT_HOME/framework-v1"          # 覆盖脚本内默认的 ~/projects-hold 路径
export PATH="\$AUTOKNIT_HOME/venv/bin:\$PATH"      # venv 前置（python3.11 + fw-* console scripts）
export DSH_HOME="\${DSH_HOME:-\$HOME/.fw-dsh}"     # 独立 dsh 环境，与其它 agent 隔离
exec bash "\$FW1/fw-tools/autoknit" "\$@"
LAUNCHER_EOF
chmod +x "$BIN_DIR/autoknit"

# ---- 4. 提示 PATH ----
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo ""
     echo "⚠ 未在 PATH 中检测到 ${BIN_DIR}，请把下面这行加入你的 shell 配置后重开终端："
     echo "   export PATH=\"\$HOME/.local/bin:\$PATH\""
     ;;
esac

# ---- 5. 结果摘要 ----
echo ""
if [ -x "$HOME/Library/Application Support/QClaw/npm-global/bin/dsh" ] || command -v dsh >/dev/null 2>&1; then
  echo "• 检测到 dsh CLI（planner/executor 真身可用）"
else
  echo "⚠ 未检测到 dsh CLI：autoknit new / run（非 demo 模式）需要 dsh 已安装并登录；demo 模式无需 dsh。"
fi
echo "✅ 安装完成：$BIN_DIR/autoknit"
echo "   用法：autoknit new <PRD.md> | run <任务目录> | status | token | demo | plan-only | summary | merge"