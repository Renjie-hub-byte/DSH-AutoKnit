"""子进程驱动（ScriptedAgentDriver）：真实 spawn executor（cwd=模块目录）+ 中断/resume。

证明"spawn executor（cwd=模块目录）"是真实进程边界：
- 子进程在模块目录落 src/ 产物、改 REVIEW、写 tmp/executor-outcome.json（cwd 生效）
- 退出码 13 → interrupted（RUN 层 checkpoint）
- CLI 级：FW_EXIT_INTERRUPT=1 → exit 130 → resume → exit 0（不重跑已完成）
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from helpers import unavailable_split_driver
from fw_runner.cli import main as cli_main
from fw_runner.drivers import ScriptedAgentDriver
from fw_runner.runner import run

# demo 驱动已随 packaging-p0 收进包内（fw_runner/scripts/，随 wheel 分发）
BIN = Path(__file__).resolve().parent.parent / "fw_runner" / "scripts"
# 脚本不再自带 sys.path hack → 用当前解释器调起（依赖随 pip 环境提供）
PY = f"{sys.executable} "


def test_subprocess_spawn_cwd_effect(single_root):
    """ScriptedAgentDriver + demo 脚本：子进程 cwd=模块目录，产物落对位置。"""
    exec_driver = ScriptedAgentDriver(PY + str(BIN / "fw-executor-demo"), role="executor")
    aud_driver = ScriptedAgentDriver(PY + str(BIN / "fw-auditor-demo"), role="auditor")

    result = run(single_root, executor_driver=exec_driver, auditor_driver=aud_driver)

    assert result.status == "complete", result.to_dict()
    mdir = single_root / "modules" / "m01-升级链样本"
    # 子进程 cwd=模块目录落盘（真实文件效应）
    assert (mdir / "src" / "demo-artifact.txt").is_file()
    assert (mdir / "tmp" / "executor-outcome.json").is_file()
    assert (mdir / "tmp" / "auditor-outcome.json").is_file()
    # 事件含 executor.round 且 executor_id=E1
    events = [json.loads(ln) for ln in
              (single_root / "总日志" / "dispatch.jsonl").read_text(encoding="utf-8").splitlines()
              if ln.strip()]
    assert any(e["event"] == "module.done" for e in events)


def test_cli_interrupt_then_resume_exit_codes(resume_root):
    """CLI 级：FW_EXIT_INTERRUPT=1 → exit 130；resume → exit 0（已完成模块不重跑）。"""
    old = os.environ.get("FW_EXIT_INTERRUPT")
    import contextlib, io
    try:
        os.environ["FW_EXIT_INTERRUPT"] = "1"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code1 = cli_main(["run", str(resume_root), "--json", "--max-parallel", "2"])
        assert code1 == 130, code1
        d1 = json.loads(buf.getvalue())
        assert d1["status"] == "interrupted"
        # 快照已写
        snap = json.loads((resume_root / "总日志" / "快照.json").read_text(encoding="utf-8"))
        assert snap["status"] == "interrupted"
        # 记录已完成的模块数（中断发生在所有模块的 executor 第 1 轮时）
        run_id = snap["run_id"]
        # m01/m02 可能已完成一批；resume 时已 done 的不重跑
        done_before = {m for m, s in snap["modules"].items() if s == "done"}
    finally:
        if old is None:
            os.environ.pop("FW_EXIT_INTERRUPT", None)
        else:
            os.environ["FW_EXIT_INTERRUPT"] = old

    # resume（无中断 env）
    buf2 = io.StringIO()
    with contextlib.redirect_stdout(buf2):
        code2 = cli_main(["run", str(resume_root), "--resume-from-checkpoint",
                          "--json", "--max-parallel", "2"])
    assert code2 == 0, code2
    d2 = json.loads(buf2.getvalue())
    assert d2["status"] == "complete"
    assert d2["run_id"] == run_id, "resume 应延续同一 run_id"
    assert set(d2["completed"]) == {"m01", "m02", "m03"}
    # 已完成模块不重跑：m01/m02（若首轮已 done）executor_round 仍为 1
    for m in done_before:
        assert d2["modules"][m]["executor_round"] == 1, f"{m} 被重跑了"


def test_driver_nonzero_exit_routes_to_upgrade(tmp_path):
    """非零退出码 → agent_error → 升级链（block/self，v1.0 含 SPLIT 路由），
    SPLIT 尝试真实拆分（缺 fw-split.sh）→ split_failed → 回人。"""
    from fw_runner.context import load_task_context
    from helpers import build_task, module
    root = build_task(tmp_path, "crash", [module("m01", "崩溃", deps=[])])
    script = tmp_path / "crash.sh"
    script.write_text("#!/bin/sh\necho boom >&2\nexit 42\n", encoding="utf-8")
    script.chmod(0o755)
    exec_driver = ScriptedAgentDriver(str(script), role="executor")
    aud_driver = ScriptedAgentDriver(PY + str(BIN / "fw-auditor-demo"), role="auditor")

    result = run(root, executor_driver=exec_driver, auditor_driver=aud_driver,
                 split_driver=unavailable_split_driver(),
                 overrides={"retry_before_switch": 1, "max_executor_switches": 0})
    # 崩溃即 block/self；retry_before_switch=1 且 max_switches=0 → switch 用尽后走 SPLIT 路由
    # （C4 真实尝试拆分，缺 fw-split.sh）→ split_failed → 回人
    assert result.status == "needs_human", result.to_dict()
    assert result.modules["m01"]["root"] == "self"
    assert "模块无法拆分" in result.modules["m01"]["reason"], result.modules["m01"]["reason"]
