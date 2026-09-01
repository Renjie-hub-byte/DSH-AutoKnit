#!/usr/bin/env python3.11
"""fw-integrate 示例：契约一致的 executor 驱动（子进程形态，cwd=模块目录）。

行为（演示真实 executor 按契约交付）：
1. 开工先读 REVIEW.md（把关键行打到 stdout，模拟"读交接/读反馈"）
2. 沿 REVIEW.md 追加"已做"并写真实产物（按本模块在任务书中的接口/基线）
3. 改写 contract.yaml 的 input.from / output.artifacts / describe（运行时契约）
4. 交付说明.md 追加基线证据文案（供预测基线对照）
5. 写 tmp/executor-outcome.json（机器可解析结果，status=ok, substance=True）

产物映射（与 examples 任务书预测基线对齐）：
  m01-数据采集 -> src/data/orders.json        （订单数据落盘为 JSON）
  m02-数据清洗 -> src/data/cleaned_orders.json（标准化订单记录）
  m03-报表输出 -> src/data/daily_orders.csv   （按日聚合订单统计 CSV）
环境变量由 runner 注入：MODULE_DIR / TASK_ROOT / RUN_ID / ROUND / ROLE / EXECUTOR_ID / MODE。
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "fw-runner"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "fw-protocol"))

from fw_runner.review import append_done, append_todo, read_review  # noqa: E402

module_dir = Path(os.environ["MODULE_DIR"])
run_id = os.environ.get("RUN_ID", "run")
round_no = os.environ.get("ROUND", "1")
executor_id = os.environ.get("EXECUTOR_ID", "E1")

mid = module_dir.name.split("-", 1)[0]
review_path = module_dir / "REVIEW.md"
print(f"[executor-conform] 开工先读 REVIEW.md: {review_path} ({executor_id} round {round_no})")
if review_path.is_file():
    for line in review_path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith(("status:", "root:", "executor_round:", "executor_id:")):
            print(f"  REVIEW | {line.strip()}")

append_todo(review_path, f"轮次 {round_no} 按验收清单执行（{run_id}）")
append_done(review_path, f"完成交付轮 {round_no}（executor={executor_id}, run={run_id}）")

ARTIFACTS = {
    "m01": ("src/data/orders.json", '[{"order_id": "A1", "amount": 12.5}]'),
    "m02": ("src/data/cleaned_orders.json", '[{"order_id": "A1", "amount": 12.5, "valid": true}]'),
    "m03": ("src/data/daily_orders.csv", "date,count,total\n2026-08-21,1,12.5\n"),
}
if mid in ARTIFACTS:
    rel, content = ARTIFACTS[mid]
    target = module_dir / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    print(f"[executor-conform] 落产物 {rel}")
else:
    (module_dir / "src").mkdir(exist_ok=True)
    (module_dir / "src" / "deliverable.txt").write_text(f"{mid} deliverable\n", encoding="utf-8")
    print(f"[executor-conform] 未知模块 {mid}，落通用产物")

# 契约填写（input.from 按任务书 dependencies；output.artifacts 按上述映射）
deps: list = []
task_yaml = Path(os.environ.get("TASK_ROOT", "") ) / "task.yaml"
if task_yaml.is_file():
    import yaml
    try:
        tdoc = yaml.safe_load(task_yaml.read_text(encoding="utf-8"))
        for m in (tdoc.get("modules") or []):
            if str(m.get("id")) == mid:
                deps = [str(d) for d in (m.get("dependencies") or [])]
                break
    except Exception:
        deps = []
contract_path = module_dir / "contract.yaml"
if contract_path.is_file():
    import yaml
    doc = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    doc.setdefault("input", {})["from"] = deps
    doc.setdefault("output", {})["artifacts"] = [rel] if mid in ARTIFACTS else ["src/deliverable.txt"]
    contract_path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False),
                             encoding="utf-8")
    print(f"[executor-conform] 契约填写 input.from={deps} output.artifacts={doc['output']['artifacts']}")

# 交付说明基线证据
delivery = module_dir / "交付说明.md"
if delivery.is_file():
    extra = {
        "m01": "\n## 交付摘要\n- 订单数据已落盘为 JSON（src/data/orders.json），结构符合契约。\n",
        "m02": "\n## 交付摘要\n- 清洗模块产出标准化订单记录（含字段校验），见 cleaned_orders.json。\n",
        "m03": "\n## 交付摘要\n- 报表模块输出按日聚合的订单统计 CSV（daily_orders.csv）。\n",
    }.get(mid, f"\n## 交付摘要\n- {mid} 交付产物见 output.artifacts。\n")
    with open(delivery, "a", encoding="utf-8") as f:
        f.write(extra)

(tmp := module_dir / "tmp").mkdir(exist_ok=True)
(tmp / "executor-outcome.json").write_text(json.dumps({
    "status": "ok", "substance": True, "tokens": 0,
    "detail": {"executor_id": executor_id, "round": int(round_no), "run_id": run_id},
}, ensure_ascii=False, indent=2), encoding="utf-8")
print("[executor-conform] done")
