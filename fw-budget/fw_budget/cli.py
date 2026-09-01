"""fw-budget CLI：预算闸门管理（需求5）。

用法:
    python3.11 -m fw_budget.cli status TASK_ROOT [--json]
    python3.11 -m fw_budget.cli add-budget TASK_ROOT --max-tokens N [--reason TEXT] [--json]
    python3.11 -m fw_budget.cli archive TASK_ROOT [--reason TEXT] [--to DIR] [--json]
    python3.11 -m fw_budget.cli resume TASK_ROOT [--extra-budget N] [--executor-cmd CMD]
                [--auditor-cmd CMD] [--max-parallel N] [--mode MODE] [--json]

退出码（机器可解析）:
    0   = ok           status 正常 / add-budget 成功 / archive 成功 / resume 完成
    1   = input_error  任务根/任务书不可读、已归档、参数非法（非 usage 语义）
    2   = human        resume 结果状态为 stopped / needs_human / integration_failed
                       （预算又停 / 回人 / 集成失败，需人工；信息在 --json）
    3   = io_error     运行期 IO / 基础设施异常
    4   = usage        CLI 用法错误
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path
from typing import Optional, Sequence

from .gate_state import BudgetInputError
from .manage import BudgetManageError, add_budget, archive, resume as resume_run, resume_advice, run_first
from .meter import DshTokenMeter
from .report import build_report, human_summary

VERSION = "1.0.0"
EXIT_OK = 0
EXIT_INPUT = 1
EXIT_HUMAN = 2
EXIT_IO = 3
EXIT_USAGE = 4

_BIN_DIR = Path(__file__).resolve().parent.parent / "bin"


def _default_cmd(name: str) -> str:
    return str(_BIN_DIR / name)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fw-budget",
        description="预算闸门管理：70% 预警（含模块消耗排行）/ 100% 硬停 / 单模块上限 / 加预算 resume / 放弃归档。",
    )
    p.add_argument("--version", action="version", version=f"fw-budget {VERSION}")

    def _usage_error(message: str) -> None:
        raise SystemExit(EXIT_USAGE)
    p.error = _usage_error
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("status", help="预算状态报告（warn/stop 判定 + 排行 + 完成/未完成/已试/token）")
    sp.add_argument("task_root")
    sp.add_argument("--json", action="store_true")

    ap = sub.add_parser("add-budget", help="人工加预算：改 task.yaml budget.max_tokens（原子写）")
    ap.add_argument("task_root")
    ap.add_argument("--max-tokens", type=int, required=True, help="新全局 max_tokens（正整数）")
    ap.add_argument("--reason", default="", help="加预算原因（写入结果/归档记录）")
    ap.add_argument("--json", action="store_true")

    kp = sub.add_parser("archive", help="放弃归档：快照标记 archived + 目录 move 到 archived/")
    kp.add_argument("task_root")
    kp.add_argument("--reason", default="", help="放弃原因")
    kp.add_argument("--to", dest="to_dir", default=None, help="归档父目录（默认任务根父目录/archived/）")
    kp.add_argument("--json", action="store_true")

    fp = sub.add_parser("run", help="首次运行：注入真实 BudgetGate（fw-runner CLI 默认 Null 闸门）")
    fp.add_argument("task_root")
    fp.add_argument("--executor-cmd", default=None, help="executor 子进程命令模板（默认 fw-runner demo 驱动）")
    fp.add_argument("--auditor-cmd", default=None, help="auditor 子进程命令模板（默认 fw-runner demo 驱动）")
    fp.add_argument("--max-parallel", type=int, default=None, help="覆盖 runtime.max_parallel")
    fp.add_argument("--mode", choices=["speed_first", "cost_first"], default="speed_first")
    fp.add_argument("--json", action="store_true")

    rp = sub.add_parser("resume", help="加预算后续跑：重建闸门（累计消耗）→ runner --resume-from-checkpoint")
    rp.add_argument("task_root")
    rp.add_argument("--extra-budget", type=int, default=None,
                    help="先加预算到该 max_tokens，再 resume（可选；也可先用 add-budget）")
    rp.add_argument("--reason", default="", help="加预算/续跑原因")
    rp.add_argument("--executor-cmd", default=None, help="executor 子进程命令模板（默认 fw-runner demo 驱动）")
    rp.add_argument("--auditor-cmd", default=None, help="auditor 子进程命令模板（默认 fw-runner demo 驱动）")
    rp.add_argument("--max-parallel", type=int, default=None, help="覆盖 runtime.max_parallel")
    rp.add_argument("--mode", choices=["speed_first", "cost_first"], default="speed_first")
    rp.add_argument("--json", action="store_true")

    return p


def _print_json(obj, exit_code: int = EXIT_OK) -> int:
    print(json.dumps(obj, ensure_ascii=False, indent=2))
    return exit_code


def _cmd_status(root: str, as_json: bool) -> int:
    report = build_report(root, meter=DshTokenMeter(root))
    if as_json:
        return _print_json(report.to_dict())
    print(human_summary(report))
    return EXIT_OK


def _cmd_add_budget(root: str, max_tokens: int, reason: str, as_json: bool) -> int:
    upd = add_budget(root, max_tokens, reason=reason)
    if as_json:
        return _print_json(upd.to_dict())
    print("fw-budget add-budget 成功")
    print(f"  任务根     : {upd.task_root}")
    print(f"  max_tokens : {upd.old_max_tokens} -> {upd.new_max_tokens}"
          f"  warn_at={upd.warn_at}  stop_at={upd.stop_at}"
          f"  per_module_max_tokens={upd.per_module_max_tokens or '未单独限制'}")
    print(f"  文件       : {upd.file}")
    if reason:
        print(f"  原因       : {reason}")
    print("  下一步     : fw-budget resume <任务根> 续跑（已完成不重跑）；不再续跑则 fw-budget archive <任务根>")
    return EXIT_OK


def _cmd_archive(root: str, reason: str, to_dir: Optional[str], as_json: bool) -> int:
    res = archive(root, reason=reason, to_dir=to_dir)
    if as_json:
        return _print_json(res.to_dict())
    print("fw-budget archive 完成（放弃归档）")
    print(f"  原路径 : {res.old_path}")
    print(f"  新路径 : {res.new_path}")
    print(f"  原因   : {res.reason or '（未填）'}")
    print(f"  快照   : status={res.snapshot_status}  run_id={res.run_id or '-'}")
    print(f"  归档说明: {res.archived_mark}")
    return EXIT_OK


def _build_scripted_drivers(executor_cmd, auditor_cmd):
    from fw_runner.drivers import ScriptedAgentDriver
    executor_driver = (ScriptedAgentDriver(executor_cmd, role="executor")
                       if executor_cmd else ScriptedAgentDriver(_default_cmd("fw-executor-demo"), role="executor"))
    auditor_driver = (ScriptedAgentDriver(auditor_cmd, role="auditor")
                      if auditor_cmd else ScriptedAgentDriver(_default_cmd("fw-auditor-demo"), role="auditor"))
    return executor_driver, auditor_driver


def _print_run_result(result, advice_text: str, as_json: bool) -> int:
    payload = {
        "ok": result.ok,
        "status": result.status,
        "exit_reason": result.exit_reason,
        "run_id": result.run_id,
        "completed": result.completed,
        "needs_human": result.needs_human,
        "tokens_used": result.tokens_used,
        "checkpoint": str(result.checkpoint),
        "modules": result.modules,
        "budget": (result.payload or {}).get("budget"),
    }
    if as_json:
        return _print_json(payload, EXIT_HUMAN if result.status in
                           ("stopped", "needs_human", "integration_failed", "needs_confirmation")
                           else EXIT_OK)
    print(f"fw-budget: status={result.status} exit_reason={result.exit_reason}")
    print(f"  run_id={result.run_id}  completed={result.completed or '（无）'}"
          f"  needs_human={result.needs_human or '（无）'}")
    print(f"  tokens_used={result.tokens_used}")
    print(f"  {advice_text}")
    if result.status in ("stopped", "needs_human", "integration_failed", "needs_confirmation"):
        return EXIT_HUMAN
    return EXIT_OK


def _cmd_run(root: str, executor_cmd: Optional[str], auditor_cmd: Optional[str],
             max_parallel: Optional[int], mode: str, as_json: bool) -> int:
    executor_driver, auditor_driver = _build_scripted_drivers(executor_cmd, auditor_cmd)
    overrides = {"max_parallel": max_parallel} if max_parallel is not None else None
    try:
        result = run_first(root, executor_driver=executor_driver,
                           auditor_driver=auditor_driver,
                           overrides=overrides, mode=mode)
    except (BudgetManageError, BudgetInputError) as e:
        return _print_json({"ok": False, "status": "input_error", "message": str(e)}, EXIT_INPUT)             if as_json else (print(f"fw-budget run: {e}", file=sys.stderr), EXIT_INPUT)[1]
    return _print_run_result(result, "首次运行（真实 BudgetGate 已注入）", as_json)


def _cmd_resume(root: str, extra_budget: Optional[int], reason: str,
                executor_cmd: Optional[str], auditor_cmd: Optional[str],
                max_parallel: Optional[int], mode: str, as_json: bool) -> int:
    # resume 前置：未归档校验（resume() 内部也校验，这里提前给友好提示）
    advice = resume_advice(root)
    overrides = {"max_parallel": max_parallel} if max_parallel is not None else None

    executor_driver, auditor_driver = _build_scripted_drivers(executor_cmd, auditor_cmd)
    try:
        result = resume_run(
            root,
            extra_max_tokens=extra_budget,
            reason=reason,
            executor_driver=executor_driver,
            auditor_driver=auditor_driver,
            overrides=overrides,
            mode=mode,
        )
    except BudgetManageError as e:
        return _print_json({"ok": False, "status": "input_error", "message": str(e)}, EXIT_INPUT) \
            if as_json else (print(f"fw-budget resume: {e}", file=sys.stderr), EXIT_INPUT)[1]
    except BudgetInputError as e:
        return _print_json({"ok": False, "status": "input_error", "message": str(e)}, EXIT_INPUT) \
            if as_json else (print(f"fw-budget resume: {e}", file=sys.stderr), EXIT_INPUT)[1]

    payload = {
        "ok": result.ok,
        "status": result.status,
        "exit_reason": result.exit_reason,
        "run_id": result.run_id,
        "completed": result.completed,
        "needs_human": result.needs_human,
        "tokens_used": result.tokens_used,
        "checkpoint": str(result.checkpoint),
        "advice": {
            "would_stop_now": advice.would_stop_now,
            "used": advice.used,
            "max_tokens": advice.max_tokens,
            "message": advice.message,
        },
        "modules": result.modules,
        "budget": (result.payload or {}).get("budget"),
    }
    if as_json:
        return _print_json(payload, EXIT_HUMAN if result.status in
                           ("stopped", "needs_human", "integration_failed", "needs_confirmation")
                           else EXIT_OK)
    print(f"fw-budget resume: status={result.status} exit_reason={result.exit_reason}")
    print(f"  run_id={result.run_id}  completed={result.completed or '（无）'}"
          f"  needs_human={result.needs_human or '（无）'}")
    print(f"  tokens_used={result.tokens_used}")
    print(f"  预算提示: {advice.message}")
    if advice.would_stop_now and extra_budget is None:
        print("  ⚠ resume 前未加预算，可能立即再次硬停；用 --extra-budget 或先 add-budget。")
    if result.status in ("stopped", "needs_human", "integration_failed", "needs_confirmation"):
        return EXIT_HUMAN
    return EXIT_OK


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        code = e.code
        return int(code) if isinstance(code, int) else EXIT_USAGE

    try:
        if args.command == "status":
            return _cmd_status(args.task_root, args.json)
        if args.command == "add-budget":
            return _cmd_add_budget(args.task_root, args.max_tokens, args.reason, args.json)
        if args.command == "archive":
            return _cmd_archive(args.task_root, args.reason, args.to_dir, args.json)
        if args.command == "run":
            return _cmd_run(args.task_root, args.executor_cmd, args.auditor_cmd,
                            args.max_parallel, args.mode, args.json)
        if args.command == "resume":
            return _cmd_resume(args.task_root, args.extra_budget, args.reason,
                               args.executor_cmd, args.auditor_cmd,
                               args.max_parallel, args.mode, args.json)
        parser.error(f"未知子命令: {args.command}")
        return EXIT_USAGE
    except (BudgetManageError, BudgetInputError) as e:
        if getattr(args, "json", False):
            return _print_json({"ok": False, "status": "input_error", "message": str(e)}, EXIT_INPUT)
        print(f"fw-budget: {e}", file=sys.stderr)
        return EXIT_INPUT
    except Exception as e:  # noqa: BLE001 —— CLI 顶层兜底，保证退出码可解析
        if getattr(args, "json", False):
            return _print_json({"ok": False, "status": "io_error",
                                "message": f"{type(e).__name__}: {e}"}, EXIT_IO)
        print(f"fw-budget: 执行失败 {type(e).__name__}: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return EXIT_IO


if __name__ == "__main__":
    sys.exit(main())
