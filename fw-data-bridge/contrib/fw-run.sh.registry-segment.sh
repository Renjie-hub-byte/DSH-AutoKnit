#!/usr/bin/env bash
# =============================================================================
# fw-run.sh 注册表登记段（m01 交付，供 merge/apply 合入 fw-run.sh 的代码树）
#
# 边界（热文件保护）：本段是代码树，由 merge/apply 阶段合回 fw-run.sh；
# 不在运行中途直接改 fw-runner / fw-executor.sh / fw-auditor.sh / fw-spawn.py。
# 仅允许在 fw-run.sh 增加注册表登记段，绝不改动既有调度逻辑。
#
# 数据契约（runs_registry，全链路唯一事实源，禁止自定义路径/字段/枚举/ts 格式）：
#   路径   ~/.autoknit/runs.json（环境变量 AUTOKNIT_RUNS_REGISTRY 覆盖绝对路径）
#   格式   {"runs": [ {record...} ]}
#   record {run_id, task_dir, task, status(active|complete|archived), started_at, updated_at}
#   status active|complete|archived；未知值不写入（程序段只按枚举登记）。
#   ts     ISO-8601 UTC（YYYY-MM-DDTHH:MM:SS+00:00）。
#   API key 绝不入库（本段只写上述契约字段，不接收/不透传任何密钥）。
#
# 用法（在 fw-run.sh 相应生命周期钩子调用）：
#   run.start:                        fw_run_registry_upsert "$RUN_ID" "$TASK_DIR" "$TASK" active
#   run 结束(complete|needs_human|exit): fw_run_registry_upsert "$RUN_ID" "$TASK_DIR" "$TASK" complete
# 程序段（非 LLM 角色），纯标准工具（bash + python3），零第三方依赖。
# =============================================================================

# 契约枚举（只允许这几种状态入注册表）。
FW_RUN_REGISTRY_STATUSES=("active" "complete" "archived")

# 幂等登记/更新一条 run 记录。首次调用（run.start）新建并记 started_at；
# 后续调用（状态更新）仅刷新 status 与 updated_at，保持 started_at 不变。
# 入参顺序固定：<run_id> <task_dir> <task> <status>。
# 返回 0 表示成功、1 表示参数非法或写盘失败（不中断 fw-run.sh 主流程）。
fw_run_registry_upsert() {
  local run_id="$1" task_dir="$2" task="$3" status="$4"
  local py

  [ -n "$run_id" ] || return 1
  [ -n "$task_dir" ] || return 1
  local ok=""
  for s in "${FW_RUN_REGISTRY_STATUSES[@]}"; do
    [ "$s" = "$status" ] && ok=1 && break
  done
  [ -n "$ok" ] || return 1

  py="$(command -v python3)" || return 1
  AUTOKNIT_RUNS_REGISTRY="${AUTOKNIT_RUNS_REGISTRY:-}" "$py" - "$run_id" "$task_dir" "$task" "$status" <<'FW_PY'
import json
import os
import sys
from datetime import datetime, timezone

run_id, task_dir, task, status = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]

def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")

path = os.environ.get("AUTOKNIT_RUNS_REGISTRY", "").strip()
path = os.path.abspath(os.path.expanduser(path)) if path else os.path.abspath(
    os.path.expanduser(os.path.join("~", ".autoknit", "runs.json")))

try:
    with open(path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
except (OSError, ValueError):
    payload = {}
records = payload.get("runs") if isinstance(payload, dict) else None
if not isinstance(records, list):
    records = []

found = None
for i, rec in enumerate(records):
    if isinstance(rec, dict) and rec.get("run_id") == run_id:
        found = i
        break

ts = now_utc()
if found is None:
    records.append({
        "run_id": run_id,
        "task_dir": task_dir,
        "task": task,
        "status": status,
        "started_at": ts,
        "updated_at": ts,
    })
else:
    rec = dict(records[found])
    rec["task_dir"] = task_dir
    rec["task"] = task
    rec["status"] = status
    rec["updated_at"] = ts
    records[found] = rec

os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
tmp = path + ".tmp"
try:
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"runs": records}, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
except OSError:
    try:
        if os.path.exists(tmp):
            os.remove(tmp)
    except OSError:
        pass
    sys.exit(1)
FW_PY
}

# === 使用示例（在 fw-run.sh 生命周期钩子中按需放开调用）===
# run.start:   fw_run_registry_upsert "${RUN_ID:-}" "${TASK_DIR:-}" "${TASK_NAME:-}" active
# 收官 complete/needs_human/exit:
#              fw_run_registry_upsert "${RUN_ID:-}" "${TASK_DIR:-}" "${TASK_NAME:-}" complete
