#!/bin/bash
# AutoKnit 数据桥演示：起本地 fw-api serve（8765）+ mock 任务数据。
# 面板(client plugin) 默认读取 127.0.0.1:8765，展示 mock 任务；点「归档」=清除该项。
# 用法：bash run-bridge-demo.sh start|stop|status
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PORT=8765
MOCK_DIR="${AUTOKNIT_MOCK_DIR:-$HOME/.dsh/profiles/web/autoknit-mock-task}"
PIDFILE="${TMPDIR:-/tmp}/fwapi-$PORT.pid"

cmd="${1:-status}"
case "$cmd" in
  start)
    if lsof -tiTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; then
      echo "already running on :$PORT"; exit 0
    fi
    mkdir -p "$MOCK_DIR/总日志"
    # 首次生成 mock 数据（若不存在）
    if [ ! -f "$MOCK_DIR/总日志/runs.json" ]; then
      cat > "$MOCK_DIR/总日志/runs.json" <<JSON
{"runs": [
  {"run_id":"run-001","stage":"executor","stage_label":"执行中","task_name":"AutoKnit 合并任务2","module_states":{"m01":{"status":"ok"},"m02":{"status":"running"}},"urgency":3,"needs_human":false,"consumption":{"token_input":4200,"token_output":1800,"cache_hit":"hit","duration_sec":152}},
  {"run_id":"run-002","stage":"needs_human","stage_label":"待决策","task_name":"DSH 插件真渲染面板","module_states":{"m01":{"status":"ok"},"m02":{"status":"done"}},"urgency":2,"needs_human":true,"consumption":{"token_input":8100,"token_output":3200,"cache_hit":"no","duration_sec":340}},
  {"run_id":"run-003","stage":"auditor","stage_label":"审查中","task_name":"全仓回归 455 测试","module_states":{"fw-panel":{"status":"ok"},"fw-api":{"status":"ok"}},"urgency":1,"needs_human":false,"consumption":{"token_input":1500,"token_output":700,"cache_hit":"hit","duration_sec":48}}
]}
JSON
      echo '{"archived": []}' > "$MOCK_DIR/总日志/archived.json"
    fi
    PYTHONPATH="$HERE/src" AUTOKNIT_TASK_DIR="$MOCK_DIR" \
      nohup /usr/local/bin/python3 -m fwapi serve --host 127.0.0.1 --port "$PORT" --task-dir "$MOCK_DIR" \
      >/tmp/fwapi-$PORT.log 2>&1 &
    echo $! > "$PIDFILE"
    sleep 2
    curl -s "http://127.0.0.1:$PORT/api/health" >/dev/null && echo "OK: fw-api 起于 :$PORT (mock=$MOCK_DIR)" || { echo "FAIL: 查看 /tmp/fwapi-$PORT.log"; exit 1; }
    ;;
  stop)
    if [ -f "$PIDFILE" ]; then kill "$(cat "$PIDFILE")" 2>/dev/null; rm -f "$PIDFILE"; fi
    pkill -f "fwapi serve --port $PORT" 2>/dev/null
    echo "stopped"
    ;;
  status)
    if lsof -tiTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; then
      echo "running on :$PORT"; curl -s "http://127.0.0.1:$PORT/api/tasks" | /usr/local/bin/python3 -c "import sys,json; d=json.load(sys.stdin); print('tasks:', [r['run_id'] for r in d])" 2>/dev/null
    else echo "not running"; fi
    ;;
  *) echo "usage: $0 start|stop|status"; exit 2;;
esac
