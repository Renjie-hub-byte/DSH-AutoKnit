#!/usr/bin/env python3.11
"""framework-v1 需求7 端到端示例：auditor 驱动（子进程形态，cwd=模块目录，只读产物区）。

演示真实 auditor 验收协议（过程审计三步 + 结果对照；三权分立：只判不写执行）：
1. 过程审计步1：读 REVIEW.md 已做节（非占位条目数 ≥1）
2. 过程审计步2：对照模块任务书-<mid>.yaml 验收清单（轻量：确认已做条目存在）
3. 结果对照：contract.yaml output.artifacts 声明的产物必须真实存在（缺 → block + blocker）
判定四段（机器可解析，被 fw-runner DriverOutcome.from_mapping 消费）：
  verdict / root / confidence / reason(+blocker)
- pass  → verdict=pass root 空 confidence=0.95 blocker 空
- block → verdict=block root=self confidence=0.5 blocker=缺失清单
只写 tmp/auditor-outcome.json（auditor 豁免区；不触碰 src/test/REVIEW 产物区）。

token 记账：每轮审计 50 tokens（dsh token-meter 对接钩子）。
环境变量由 runner 注入：MODULE_DIR / TASK_ROOT / RUN_ID / ROUND / ROLE / EXECUTOR_ID / MODE。
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "fw-runner"))

import yaml  # noqa: E402
from fw_runner.review import read_review  # noqa: E402

module_dir = Path(os.environ["MODULE_DIR"])
run_id = os.environ.get("RUN_ID", "run")
review_path = module_dir / "REVIEW.md"
doc = read_review(review_path) if review_path.is_file() else None
done_entries = [ln for ln in (doc.list_done() if doc else []) if ln.strip() not in ("- （占位）",)]

# 结果对照：contract.yaml output.artifacts 声明 vs 真实产物存在性
declared: list = []
missing: list = []
contract_path = module_dir / "contract.yaml"
if contract_path.is_file():
    try:
        cdoc = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
        declared = [a for a in ((cdoc or {}).get("output") or {}).get("artifacts") or []]
    except Exception:  # noqa: BLE001
        declared = []
missing = [a for a in declared if not (module_dir / a).is_file()]

print(f"[e2e-auditor] 审计 {module_dir.name} run={run_id} done={len(done_entries)} "
      f"declared={declared} missing={missing}")

if done_entries and declared and not missing:
    outcome = {"status": "ok", "verdict": "pass", "root": "", "confidence": 0.95,
               "reason": "过程审计三步+结果对照通过：REVIEW 已做齐全且契约产物全部真实存在",
               "blocker": "", "tokens": 50,
               "detail": {"done_entries": len(done_entries), "declared": declared}}
else:
    reason = "缺已做条目（过程审计步1）" if not done_entries else (
        "契约未声明产物（结果对照）" if not declared
        else "契约产物缺失: " + ", ".join(missing))
    outcome = {"status": "ok", "verdict": "block", "root": "self", "confidence": 0.5,
               "reason": "验收不通过：" + reason, "blocker": reason, "tokens": 50,
               "detail": {"done_entries": len(done_entries), "declared": declared,
                          "missing": missing}}

(tmp := module_dir / "tmp").mkdir(exist_ok=True)
(tmp / "auditor-outcome.json").write_text(json.dumps(outcome, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
print(f"[e2e-auditor] verdict={outcome['verdict']} root={outcome['root']} tokens={outcome['tokens']}")
