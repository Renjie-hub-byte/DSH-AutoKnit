#!/usr/bin/env python3.11
"""fw-integrate 示例：契约一致的 auditor 驱动（子进程形态，cwd=模块目录）。

行为（演示验收协议三步 + 结果对照）：读 REVIEW 已做节 + 产物存在 + 契约 output.artifacts
与真实产物一致 → pass（confidence 0.95）；任一缺失 → block（root=self + blocker，四段式）。
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "fw-runner"))

from fw_runner.review import read_review  # noqa: E402

module_dir = Path(os.environ["MODULE_DIR"])
run_id = os.environ.get("RUN_ID", "run")
review_path = module_dir / "REVIEW.md"
doc = read_review(review_path) if review_path.is_file() else None
done_entries = [ln for ln in (doc.list_done() if doc else []) if ln.strip() not in ("- （占位）",)]

# 结果对照：contract.yaml output.artifacts 声明的产物必须真实存在
declared = []
contract_path = module_dir / "contract.yaml"
if contract_path.is_file():
    import yaml
    try:
        cdoc = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
        declared = [a for a in (cdoc.get("output") or {}).get("artifacts") or []]
    except Exception:
        declared = []
missing = [a for a in declared if not (module_dir / a).is_file()]

print(f"[auditor-conform] 审计 {module_dir.name} run={run_id} done={len(done_entries)} "
      f"declared={declared} missing={missing}")

if done_entries and declared and not missing:
    outcome = {"status": "ok", "verdict": "pass", "root": "", "confidence": 0.95,
               "reason": "契约与产物一致：output.artifacts 全部存在且 REVIEW 已做齐全",
               "blocker": "", "tokens": 0,
               "detail": {"done_entries": len(done_entries), "declared": declared}}
else:
    reason = "缺已做条目" if not done_entries else (
        "契约未声明产物" if not declared else "契约产物缺失: " + ", ".join(missing))
    outcome = {"status": "ok", "verdict": "block", "root": "self", "confidence": 0.5,
               "reason": "验收不通过：" + reason, "blocker": reason, "tokens": 0,
               "detail": {"done_entries": len(done_entries), "declared": declared,
                          "missing": missing}}

(tmp := module_dir / "tmp").mkdir(exist_ok=True)
(tmp / "auditor-outcome.json").write_text(json.dumps(outcome, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
print(f"[auditor-conform] verdict={outcome['verdict']} root={outcome['root']}")
