"""fw-integrate CLI：集成验收（需求6）。

用法:
    python3.11 -m fw_integrate.cli check TASK_ROOT [--json]
    python3.11 -m fw_integrate.cli complete TASK_ROOT [--reason TEXT] [--json]
    python3.11 -m fw_integrate.cli run TASK_ROOT [--executor-cmd CMD] [--auditor-cmd CMD]
                [--mode MODE] [--json]

语义：
- check   ：运行时契约校验（接口匹配/数据格式/跨模块数据依赖）+ 预测基线对照；向
             总日志/integration.jsonl 追加 integration.check 事件；失败（exit 2）时
             --json 的 errors 明确指出“哪两个模块接口不匹配”（验收1）。
- complete：全部通过 → 完成报告 + 归档（复用 fw-budget 归档机制）；end_gate=always →
             只出完成报告请人工确认（exit 2）；有 error → 不归档回人（exit 2）（验收3）。
- run     ：全流程 —— fw-runner 编排（注入 FwIntegrateHook）→ 通过后 complete 归档。
- confirm ：end_gate=always 人工确认 —— 快照 needs_confirmation → 检查全通过 →
             完成报告 + 归档（end_gate=always 的闭环入口；auto 请用 complete）。

退出码（机器可解析，与 fw-runner/fw-budget 对齐）:
    0   = ok           检查通过 / 完成归档成功
    1   = input_error  任务根/契约/产物缺失、任务书复校验失败、快照非 complete
    2   = needs_human  集成失败（接口不匹配等） / end_gate=always 待人工确认 / runner 回人
    3   = io_error     运行期 IO / 意外异常
    4   = usage        CLI 用法错误
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path
from typing import Optional, Sequence

from .archive import IntegrateFailed, complete_and_archive, confirm_and_archive
from .context import IntegrateInputError, load_integrate_context
from .report import append_integration_event, run_checks

VERSION = "1.0.0"
EXIT_OK = 0
EXIT_INPUT = 1
EXIT_HUMAN = 2
EXIT_IO = 3
EXIT_USAGE = 4

_FW1_ROOT = Path(__file__).resolve().parent.parent.parent


def _default_cmd(name: str) -> str:
    return str(_FW1_ROOT / "fw-runner" / "bin" / name)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fw-integrate",
        description="集成验收：运行时契约校验（接口匹配/数据格式/跨模块数据依赖）+ 预测基线对照 + 完成归档。",
    )
    p.add_argument("--version", action="version", version=f"fw-integrate {VERSION}")

    def _usage_error(message: str) -> None:
        raise SystemExit(EXIT_USAGE)
    p.error = _usage_error
    sub = p.add_subparsers(dest="command", required=True)

    cp = sub.add_parser("check", help="运行时契约校验 + 基线对照（不归档）")
    cp.add_argument("task_root", help="任务根目录（fw-scaffold 生成 任务-<名>_<日期>/）")
    cp.add_argument("--json", action="store_true", help="输出机器可解析 JSON")

    ep = sub.add_parser("complete", help="全部通过 → 完成报告 + 归档（复用 fw-budget 归档机制）")
    ep.add_argument("task_root")
    ep.add_argument("--reason", default="", help="归档原因（写入归档说明）")
    ep.add_argument("--json", action="store_true")

    rp = sub.add_parser("run", help="全流程：fw-runner 编排（注入集成钩子）→ 通过后完成归档")
    rp.add_argument("task_root")
    rp.add_argument("--executor-cmd", default=None, help="executor 子进程命令模板（默认 fw-runner demo）")
    rp.add_argument("--auditor-cmd", default=None, help="auditor 子进程命令模板（默认 fw-runner demo）")
    rp.add_argument("--mode", choices=["speed_first", "cost_first"], default="speed_first")
    rp.add_argument("--reason", default="", help="归档原因")
    rp.add_argument("--json", action="store_true")

    fp = sub.add_parser("confirm", help="end_gate=always 人工确认 → 完成报告 + 归档")
    fp.add_argument("task_root")
    fp.add_argument("--reason", default="", help="归档原因（人工确认备注）")
    fp.add_argument("--json", action="store_true")
    return p


def _print(obj: dict, code: int) -> int:
    print(json.dumps(obj, ensure_ascii=False, indent=2))
    return code


def _human_report(report) -> str:
    lines = [
        "fw-integrate 集成验收报告",
        f"任务根     : {report.task_root}",
        f"总体       : {'通过' if report.ok else '失败'}",
        f"接口匹配   : {'通过' if report.interface.ok else '失败（' + str(len(report.interface.errors)) + ' error）'}",
        f"数据格式   : {'通过' if report.data_format.ok else '失败（' + str(len(report.data_format.errors)) + ' error）'}",
        f"数据依赖   : {'通过' if report.data_dependency.ok else '失败（' + str(len(report.data_dependency.errors)) + ' error）'}",
        f"预测基线   : matched={len(report.baseline.matched)} missing={len(report.baseline.missing)} "
        f"clean={len(report.baseline.clean)} violation={len(report.baseline.violations)}",
    ]
    if report.baseline.matched:
        lines.append("匹配清单（will_have）:")
        for b in report.baseline.items:
            if b.kind == "will_have" and b.status == "matched":
                ev = "; ".join(b.evidence) if b.evidence else "（关键词级命中）"
                lines.append(f"  + {b.item}  -> {ev}")
    if report.baseline.missing:
        lines.append("缺失清单（will_have）:")
        for b in report.baseline.items:
            if b.kind == "will_have" and b.status == "missing":
                lines.append(f"  - {b.item}")
    if report.baseline.violations:
        lines.append("违反清单（will_not_have 命中）:")
        for b in report.baseline.items:
            if b.kind == "will_not_have" and b.status == "violation":
                lines.append(f"  ! {b.item}  -> {'; '.join(b.evidence)}")
    for f in (report.interface.errors + report.data_format.errors + report.data_dependency.errors):
        lines.append(f"[error] {f.message}")
    for f in (report.interface.warnings + report.data_format.warnings
              + report.data_dependency.warnings):
        lines.append(f"[warn ] {f.message}")
    return "\n".join(lines)


def _cmd_check(root: str, as_json: bool) -> int:
    ic = load_integrate_context(root, require_complete=False)
    report = run_checks(ic)
    run_id = str(ic.snapshot.get("run_id") or "")
    append_integration_event(ic.task_root, report, end_gate=ic.end_gate, run_id=run_id)
    if as_json:
        return _print(report.to_dict(), EXIT_OK if report.ok else EXIT_HUMAN)
    print(_human_report(report))
    return EXIT_OK if report.ok else EXIT_HUMAN


def _cmd_complete(root: str, reason: str, as_json: bool) -> int:
    res = complete_and_archive(root, reason=reason)
    if as_json:
        return _print(res.to_dict(), EXIT_OK if res.ok and res.status == "completed" else EXIT_HUMAN)
    print(f"fw-integrate complete: status={res.status}")
    print(f"  {res.message}")
    if res.archived_path:
        print(f"  归档路径 : {res.archived_path}")
    if res.completion_report:
        print(f"  完成报告 : {res.completion_report}")
    return EXIT_OK if res.ok and res.status == "completed" else EXIT_HUMAN


def _cmd_run(root: str, executor_cmd: Optional[str], auditor_cmd: Optional[str],
             mode: str, reason: str, as_json: bool) -> int:
    from fw_runner.drivers import ScriptedAgentDriver
    from fw_runner.runner import run as runner_run

    from .hook import FwIntegrateHook

    executor_driver = (ScriptedAgentDriver(executor_cmd, role="executor")
                       if executor_cmd else ScriptedAgentDriver(_default_cmd("fw-executor-demo"), role="executor"))
    auditor_driver = (ScriptedAgentDriver(auditor_cmd, role="auditor")
                      if auditor_cmd else ScriptedAgentDriver(_default_cmd("fw-auditor-demo"), role="auditor"))
    result = runner_run(root, executor_driver=executor_driver,
                        auditor_driver=auditor_driver, mode=mode,
                        integration_hook=FwIntegrateHook())
    if result.status == "complete":
        return _cmd_complete(root, reason, as_json)
    # runner 未完整通过：回人/停/集成失败/待确认 → exit 2（信息在 --json）
    if as_json:
        payload = {
            "ok": False, "status": result.status, "exit_reason": result.exit_reason,
            "run_id": result.run_id, "completed": result.completed,
            "needs_human": result.needs_human,
            "integration": result.integration,
        }
        return _print(payload, EXIT_HUMAN)
    print(f"fw-integrate run: status={result.status} exit_reason={result.exit_reason}")
    print(f"  run_id={result.run_id} completed={result.completed or '（无）'} needs_human={result.needs_human or '（无）'}")
    if result.integration:
        status = result.integration.get("status")
        notes = result.integration.get("notes") or []
        print(f"  集成钩子: status={status}")
        for n in notes[:10]:
            print(f"    - {n}")
    print("  处理建议：解决上述问题后 --resume-from-checkpoint 或修复契约/产物后再 complete")
    return EXIT_HUMAN


def _cmd_confirm(root: str, reason: str, as_json: bool) -> int:
    res = confirm_and_archive(root, reason=reason)
    if as_json:
        return _print(res.to_dict(), EXIT_OK if res.ok else EXIT_HUMAN)
    print(f"fw-integrate confirm: status={res.status}")
    print(f"  {res.message}")
    if res.archived_path:
        print(f"  归档路径 : {res.archived_path}")
    if res.completion_report:
        print(f"  完成报告 : {res.completion_report}")
    return EXIT_OK if res.ok else EXIT_HUMAN


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        code = e.code
        return int(code) if isinstance(code, int) else EXIT_USAGE

    try:
        if args.command == "check":
            return _cmd_check(args.task_root, args.json)
        if args.command == "complete":
            return _cmd_complete(args.task_root, args.reason, args.json)
        if args.command == "run":
            return _cmd_run(args.task_root, args.executor_cmd, args.auditor_cmd,
                            args.mode, args.reason, args.json)
        if args.command == "confirm":
            return _cmd_confirm(args.task_root, args.reason, args.json)
        parser.error(f"未知子命令: {args.command}")
        return EXIT_USAGE
    except (IntegrateInputError, IntegrateFailed) as e:
        if getattr(args, "json", False):
            return _print({"ok": False, "status": "input_error", "message": str(e)}, EXIT_INPUT)
        print(f"fw-integrate: {e}", file=sys.stderr)
        return EXIT_INPUT
    except Exception as e:  # noqa: BLE001 —— CLI 顶层兜底，退出码保持可解析
        if getattr(args, "json", False):
            return _print({"ok": False, "status": "io_error",
                           "message": f"{type(e).__name__}: {e}"}, EXIT_IO)
        print(f"fw-integrate: 执行失败 {type(e).__name__}: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return EXIT_IO


if __name__ == "__main__":
    sys.exit(main())
