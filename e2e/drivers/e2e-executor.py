#!/usr/bin/env python3.11
"""framework-v1 需求7 端到端示例：executor 驱动（子进程形态，cwd=模块目录）。

演示真实 executor 执行纪律（三权分立下的 executor 协议）：
1. 开工**先读 REVIEW.md**（把机器键打到 stdout，模拟"读交接/读反馈"）
2. 把验收清单拆成可执行 todo（append_todo）
3. 干活：按模块写契约（input.from / output.artifacts）与真实产物
4. 自测外部验收：交付说明.md 追加证据文案（供 fw-integrate 预测基线对照）
5. 写 tmp/executor-outcome.json（机器可解析：status/substance/tokens/detail）

升级链演示（确定性脚本化 agent，等价真实 LLM agent 的行为位）：
- m02 且 executor=E1：前两轮"契约声明产物但产物未落盘"（模拟 executor 无法完成该交付）
  → auditor 连续打回 2 次 → runner 换 E2（交接三件套：REVIEW/contract/交付说明）
- m02 且 executor=E2：真实落盘 cleaned_orders.json（模拟换人后修复交付）→ auditor 通过
- m01 / m03：一轮真实交付

token 记账（dsh token-meter 对接钩子）：每轮 outcome.tokens 写确定值
  executor 轮：m01=250 / m02=200 / m03=200（模拟该模块每轮 token 消耗）
环境变量由 runner 注入：MODULE_DIR / TASK_ROOT / RUN_ID / ROUND / ROLE / EXECUTOR_ID / MODE。
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "fw-runner"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "fw-protocol"))

import yaml  # noqa: E402
from fw_runner.review import append_done, append_todo, read_review  # noqa: E402

module_dir = Path(os.environ["MODULE_DIR"])
task_root = Path(os.environ.get("TASK_ROOT", "."))
run_id = os.environ.get("RUN_ID", "run")
round_no = int(os.environ.get("ROUND", "1"))
executor_id = os.environ.get("EXECUTOR_ID", "E1")

mid = module_dir.name.split("-", 1)[0]
review_path = module_dir / "REVIEW.md"
print(f"[e2e-executor] 开工先读 REVIEW.md: {review_path} ({executor_id} round {round_no})")
if review_path.is_file():
    for line in review_path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith(("status:", "root:", "executor_round:", "executor_id:")):
            print(f"  REVIEW | {line.strip()}")

# ---- 契约：input.from 按任务书 dependencies；output.artifacts 按模块映射 ----
ARTIFACTS = {
    "m01": ("src/data/orders.json", '[{"order_id": "A1", "amount": 12.5}]\n'),
    "m02": ("src/data/cleaned_orders.json",
            '[{"order_id": "A1", "amount": 12.5, "valid": true, "drop_reason": null}]\n'),
    "m03": ("src/data/daily_orders.csv", "date,count,total\n2026-08-21,1,12.5\n"),
}
deps: list = []
try:
    tdoc = yaml.safe_load((task_root / "task.yaml").read_text(encoding="utf-8"))
    for m in (tdoc.get("modules") or []):
        if str(m.get("id")) == mid:
            deps = [str(d) for d in (m.get("dependencies") or [])]
            break
except Exception:  # noqa: BLE001 —— 任务书读取失败不阻断（契约缺省空依赖）
    deps = []

contract_path = module_dir / "contract.yaml"
rel, content = ARTIFACTS[mid]
if contract_path.is_file():
    doc = {}
    try:
        doc = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        doc = {}
    doc = doc if isinstance(doc, dict) else {}
    doc.setdefault("input", {})["from"] = deps
    doc["input"]["describe"] = "上游订单数据（JSON 数组，含 order_id/amount）"
    doc.setdefault("output", {})["artifacts"] = [rel]
    doc["output"]["describe"] = "模块交付产物（Json/CSV，结构见契约区）"
    contract_path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False),
                             encoding="utf-8")
    print(f"[e2e-executor] 契约填写 input.from={deps} output.artifacts={[rel]}")

# ---- 干活：待办登记 + 已做登记（实质产出 = REVIEW 变更 + 产物/契约写盘）----
append_todo(review_path, f"轮次 {round_no} 按任务书-{mid}.yaml 验收清单执行（{run_id}）")

is_m02_handover_phase = (mid == "m02" and executor_id == "E1")
if is_m02_handover_phase:
    # 升级链演示：E1 阶段"契约声明产物但未落盘"——模拟 executor 能力不足交付（auditor 打回依据）
    append_done(review_path, f"完成清洗模块编码（{executor_id} 第 {round_no} 轮，{run_id}；产物待落盘）")
    print("[e2e-executor] m02/E1：契约已声明产物但本轮未落盘（等待 auditor 打回 → 升级链）")
else:
    # 正常交付：写真实产物 + 交付说明证据
    target = module_dir / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    append_done(review_path, f"完成{mid}交付（{executor_id} 第 {round_no} 轮，{run_id}）")
    delivery = module_dir / "交付说明.md"
    if delivery.is_file():
        extra = {
            "m01": "\n## 交付摘要\n- 订单数据已落盘为 JSON（src/data/orders.json），结构符合契约。\n",
            "m02": "\n## 交付摘要\n- 清洗模块产出标准化订单记录（含字段校验），非法记录标记 drop_reason。\n",
            "m03": "\n## 交付摘要\n- 报表模块输出按日聚合的订单统计 CSV（daily_orders.csv）。\n",
        }.get(mid, "\n## 交付摘要\n- 交付产物见 output.artifacts。\n")
        with open(delivery, "a", encoding="utf-8") as f:
            f.write(extra)
    print(f"[e2e-executor] 落产物 {rel}")

# ---- token 记账（dsh token-meter 对接钩子：每轮消耗由驱动上报）----
TOKENS = {"m01": 250, "m02": 200, "m03": 200}
tokens = TOKENS.get(mid, 200)

(tmp := module_dir / "tmp").mkdir(exist_ok=True)
(tmp / "executor-outcome.json").write_text(json.dumps({
    "status": "ok",
    "substance": True,
    "tokens": tokens,
    "detail": {"executor_id": executor_id, "round": round_no, "run_id": run_id,
               "mid": mid, "handover_phase": is_m02_handover_phase},
}, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"[e2e-executor] done tokens={tokens}")
