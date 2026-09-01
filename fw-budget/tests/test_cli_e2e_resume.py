"""CLI 级端到端（auditor 可独立复现）：fw-budget run → 预算硬停 → add-budget → resume 零重跑。

用真实脚本驱动（ScriptedAgentDriver 子进程，cwd=模块目录）走完整 CLI 用户路径，
不依赖 inline harness —— 验证文档里的快速开始流程。
"""
from __future__ import annotations

import json
from pathlib import Path

from fw_budget.cli import main as cli_main
from helpers import build_task, module

EXEC_TOKENS = {"m01": 300, "m02": 400, "m03": 300}   # +audit 10 → m01 310, m02 410 → 720/700 stop


def _write_drivers(tmp_path: Path):
    """写两个脚本驱动：executor 注入 tokens，auditor 恒 pass。"""
    exec_py = tmp_path / "exec.py"
    exec_py.write_text(
        "#!/usr/bin/env python3.11\n"
        "import json, os\n"
        "from pathlib import Path\n"
        "mid = os.environ['MODULE_ID']\n"
        "d = Path(os.environ['MODULE_DIR'])\n"
        "try:\n"
        "    from fw_runner.review import append_done\n"
        "    append_done(d / 'REVIEW.md', f'cli e2e exec {mid} r{os.environ[\"ROUND\"]}')\n"
        "except Exception:\n"
        "    pass\n"
        "tokens = " + repr(EXEC_TOKENS) + ".get(mid, 300)\n"
        "(d / 'tmp').mkdir(exist_ok=True)\n"
        "(d / 'tmp' / 'executor-outcome.json').write_text(\n"
        "    json.dumps({'status': 'ok', 'substance': True, 'tokens': tokens}), encoding='utf-8')\n",
        encoding="utf-8")
    audit_py = tmp_path / "audit.py"
    audit_py.write_text(
        "#!/usr/bin/env python3.11\n"
        "import json, os\n"
        "from pathlib import Path\n"
        "d = Path(os.environ['MODULE_DIR'])\n"
        "(d / 'tmp').mkdir(exist_ok=True)\n"
        "(d / 'tmp' / 'auditor-outcome.json').write_text(\n"
        "    json.dumps({'status': 'ok', 'verdict': 'pass', 'root': '', 'confidence': 0.9,\n"
        "                'reason': 'CLI e2e', 'tokens': 10}), encoding='utf-8')\n",
        encoding="utf-8")
    return exec_py, audit_py


def test_cli_full_path_run_stop_resume(tmp_path):
    root = build_task(tmp_path, "CLI-预算端到端",
                      [module("m01", "甲", deps=[]), module("m02", "乙", deps=[]),
                       module("m03", "丙", deps=[])],
                      runtime={"max_parallel": 1, "executor_max_rounds": 5,
                               "retry_before_switch": 2, "max_executor_switches": 1,
                               "end_gate": "auto"},
                      budget={"max_tokens": 700, "warn_at": 0.7, "stop_at": 1.0})
    exec_py, audit_py = _write_drivers(tmp_path)

    # ---- 阶段1：fw-budget run → 预算硬停（exit 2）----
    code = cli_main(["run", str(root), "--executor-cmd", f"python3.11 {exec_py}",
                     "--auditor-cmd", f"python3.11 {audit_py}", "--json"])
    assert code == 2, f"预算硬停应 exit 2，实际 {code}"

    # ---- 阶段2：add-budget 700 → 2000 ----
    code = cli_main(["add-budget", str(root), "--max-tokens", "2000",
                     "--reason", "审计复核后追加", "--json"])
    assert code == 0

    # ---- 阶段3：status 复核（phase=ok）----
    code = cli_main(["status", str(root), "--json"])
    assert code == 0

    # ---- 阶段4：resume → complete（exit 0，零重跑）----
    code = cli_main(["resume", str(root), "--executor-cmd", f"python3.11 {exec_py}",
                     "--auditor-cmd", f"python3.11 {audit_py}", "--json"])
    assert code == 0, f"resume 应 complete exit 0，实际 {code}"

    # ---- 零重跑证据：事件流 executor.round.done 仅 3 次（m01/m02/m03 各 1）----
    events = [json.loads(ln) for ln in
              (root / "总日志" / "dispatch.jsonl").read_text(encoding="utf-8").splitlines()
              if ln.strip() and "seq" in json.loads(ln)]
    exec_done = [e for e in events if e["event"] == "executor.round.done"]
    assert [(e["module"], e["detail"]["round"]) for e in exec_done] == [
        ("m01", 1), ("m02", 1), ("m03", 1)]
    # resume 事件存在，seq 从 budget.stop 后延续
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    resumes = [e for e in events if e["event"] == "run.resume"]
    assert len(resumes) == 1 and resumes[0]["detail"]["snapshot_status"] == "stopped"
    # 每个模块 REVIEW 只有 1 条 exec 痕迹（零重跑）
    from fw_runner.review import read_review
    from fw_runner.context import load_task_context
    ctx = load_task_context(root)
    for mid, spec in ctx.modules.items():
        done = [ln for ln in read_review(spec.review_path).list_done() if "cli e2e" in ln]
        assert len(done) == 1, f"{mid} 应只有 1 条 exec 痕迹，实际 {len(done)}"
