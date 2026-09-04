"""fw-runner CLI：执行编排主循环。

用法:
    python3.11 -m fw_runner.cli run TASK_ROOT [选项]
    ./bin/fw-runner run TASK_ROOT [选项]

退出码（机器可解析）:
    0   = run_complete          全部模块完成，集成钩子通过/延迟，无回人
    1   = input_error           任务根/任务书不可运行（校验失败、目录缺失、配置非法）
    2   = needs_human           回人（升级链上限 / upstream|contract 根因 / end_gate=always
                                人工确认 / 集成失败 / 预算硬停）——信息在 --json payload 与快照
    3   = io/infra_error        运行期 IO / 基础设施异常（意外异常）
    4   = usage                 CLI 用法错误
    130 = interrupted           运行被中断（已写快照，--resume-from-checkpoint 续跑）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Optional, Sequence

from .context import RunnerInputError
from .drivers import ScriptedAgentDriver
from .runner import RunInterrupted, run as run_runner

VERSION = "1.0.0"
EXIT_COMPLETE = 0
EXIT_INPUT = 1
EXIT_HUMAN = 2
EXIT_IO = 3
EXIT_USAGE = 4
EXIT_INTERRUPTED = 130

_SCRIPTS_DIR = Path(__file__).resolve().parent / "scripts"   # 包内数据文件（随 wheel 分发）


def _default_cmd(name: str) -> str:
    """默认驱动命令：`<当前解释器> 包内scripts/<name>`（packaging-p0）。

    - 脚本已收进包内 fw_runner/scripts/（随 wheel 分发）；FW_RUNNER_SCRIPTS_DIR 可整体覆盖目录。
    - 显式用 sys.executable 调起：脚本自身不再做 sys.path hack，依赖fw_protocol 等
      随 pip 环境提供 —— 必须与 runner 同解释器，否则 shebang 落到系统 python3 会缺依赖。
    """
    override = os.environ.get("FW_RUNNER_SCRIPTS_DIR")
    base = Path(override) if override else _SCRIPTS_DIR
    return f"{sys.executable} {base / name}"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fw-runner",
        description="执行编排主循环：依赖图拓扑 → 并行调度 → 升级链 → checkpoint/resume。",
    )
    p.add_argument("--version", action="version", version=f"fw-runner {VERSION}")

    def _usage_error(message: str) -> None:
        raise SystemExit(EXIT_USAGE)
    p.error = _usage_error
    sub = p.add_subparsers(dest="command", required=True)
    rp = sub.add_parser("run", help="运行任务编排", description="运行任务编排")
    rp.add_argument("task_root", help="任务根目录（fw-scaffold 生成的 任务-<名>_<日期>/）")
    rp.add_argument("--resume-from-checkpoint", action="store_true",
                    help="从 总日志/快照.json 接续（已完成模块不重跑）")
    rp.add_argument("--max-parallel", type=int, default=None, help="最大并行模块数（覆盖 runtime.max_parallel）")
    rp.add_argument("--executor-max-rounds", type=int, default=None,
                    help="单模块 executor 轮数上限（超了判卡循环换人/回人）")
    rp.add_argument("--retry-before-switch", type=int, default=None,
                    help="auditor 打回 N 次后换 executor（默认 2）")
    rp.add_argument("--max-executor-switches", type=int, default=None,
                    help="最多换几个 executor，再卡回人（默认 1）")
    rp.add_argument("--heartbeat-n", type=int, default=None,
                    help="连续 N 轮无实质产出判静默卡死（默认 2）")
    rp.add_argument("--checkpoint-every", type=int, default=None,
                    help="每 N 模块完成写一次快照（默认 1）")
    # —— 分块/拆分参数（AutoKnit 可配，2026-08-28）——
    rp.add_argument("--split-exit-threshold", type=int, default=None,
                    help="剩余行数 ≤ N → final 收官续做，>N → split（默认 1000）")
    rp.add_argument("--retry-remaining-threshold", type=int, default=None,
                    help="partial 剩余交付物 ≤ N 项 → 原 executor 续做（默认 2）")
    rp.add_argument("--split-max-depth", type=int, default=None,
                    help="最大递归拆分深度（防无限递归，默认 2）")
    rp.add_argument("--split-max-total", type=int, default=None,
                    help="任务级模块总数上限（planner + split 全部），防失控递归，默认 30")
    rp.add_argument("--split-protocol-retries", type=int, default=None,
                    help="拆解协议故障回喂 LLM 的重试次数（默认 2，总尝试 3 次）；0=不回喂直接失败")
    rp.add_argument("--no-split", action="store_true",
                    help="禁用自动拆分（enable_split=False），超量直接回人")
    rp.add_argument("--enable-split", action="store_true", default=None,
                    help="显式启用自动拆分（默认开）")
    rp.add_argument("--mode", choices=["speed_first", "cost_first"], default="speed_first",
                    help="模式开关：speed_first=吞吐优先（默认）；cost_first=省 token/会话")
    rp.add_argument("--end-gate", choices=["auto", "always"], default=None,
                    help="auto=异常才回人 / always=每任务人工确认（默认 auto）")
    rp.add_argument("--executor-cmd", default=None,
                    help="executor 子进程命令模板（默认内置 demo 驱动）")
    rp.add_argument("--auditor-cmd", default=None,
                    help="auditor 子进程命令模板（默认内置 demo 驱动）")
    rp.add_argument("--json", action="store_true", help="输出机器可解析 JSON")
    # --version 只保留顶层一个（原 run 子命令上的重复定义已去重，packaging-p0）
    return p


def _collect_overrides(args) -> dict:
    ov: dict = {}
    for key in ("max_parallel", "executor_max_rounds", "retry_before_switch",
                "max_executor_switches", "heartbeat_n_rounds", "checkpoint_every", "end_gate",
                "split_exit_threshold", "retry_remaining_threshold", "split_max_depth",
                "split_max_total",
                "split_protocol_retries", "audit_require_evidence"):
        val = getattr(args, key, None)
        if val is not None:
            ov[key] = val
    # enable_split 三态：--no-split → False；--enable-split → True；都没给 → 不覆盖
    if getattr(args, "enable_split", None) is True:
        ov["enable_split"] = True
    if getattr(args, "no_split", None) is True:
        ov["enable_split"] = False
    return ov


def _human(result) -> str:
    from .model import RunnerResult
    lines = [
        f"fw-runner {VERSION} — 执行编排主循环",
        f"run_id     : {result.run_id}",
        f"任务根     : {result.task_root}",
        f"状态       : {result.status}（exit_reason={result.exit_reason}）",
        f"已完成     : {', '.join(result.completed) if result.completed else '（无）'}",
        f"回人       : {', '.join(result.needs_human) if result.needs_human else '（无）'}",
        f"失败过     : {', '.join(result.failed) if result.failed else '（无）'}",
        f"token 用量 : {result.tokens_used}",
        f"事件数     : {result.seq_events}（seq 单调递增）",
        f"耗时       : {result.duration_s:.2f}s",
        f"快照       : {result.checkpoint}",
        "",
        "模块明细:",
    ]
    for mid, info in result.modules.items():
        lines.append(
            f"  - {mid}: {info['status']}  exec={info['executor_id'] or '未分配'} "
            f"轮数={info['executor_round']} 打回={info['block_total']} 换人={info['executor_switches']} "
            f"根因={info['root'] or '无'}  reason={info['reason'][:60] or '-'}")
    if result.needs_human:
        lines.append("")
        lines.append("!! 存在回人模块（信息完备：完成/未完成/已试轮数/token 见上表与快照）")
        lines.append("   --resume-from-checkpoint 可接续；upstream/contract 根因先解决上游/契约再续")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:      # 用法错误 → 退出码 4（机器可解析）
        code = e.code
        return int(code) if isinstance(code, int) else EXIT_USAGE
    if args.command != "run":
        parser.error(f"未知子命令: {args.command}")

    overrides = _collect_overrides(args)
    executor_driver = (ScriptedAgentDriver(args.executor_cmd, role="executor")
                       if args.executor_cmd else ScriptedAgentDriver(_default_cmd("fw-executor-demo"), role="executor"))
    auditor_driver = (ScriptedAgentDriver(args.auditor_cmd, role="auditor")
                      if args.auditor_cmd else ScriptedAgentDriver(_default_cmd("fw-auditor-demo"), role="auditor"))

    try:
        result = run_runner(
            args.task_root,
            overrides=overrides,
            mode=args.mode,
            resume=args.resume_from_checkpoint,
            executor_driver=executor_driver,
            auditor_driver=auditor_driver,
        )
    except RunInterrupted as e:
        # run() 内部已捕获并返回 interrupted 结果；此处兜底（不应到达）
        if args.json:
            print(json.dumps({"ok": False, "status": "interrupted", "exit_reason": "interrupted",
                              "message": str(e)}, ensure_ascii=False, indent=2))
        else:
            print(f"fw-runner: 运行被中断: {e}", file=sys.stderr)
        return EXIT_INTERRUPTED
    except RunnerInputError as e:
        if args.json:
            print(json.dumps({"ok": False, "status": "input_error",
                              "exit_reason": "input_error", "message": str(e)},
                             ensure_ascii=False, indent=2))
        else:
            print(f"fw-runner: 输入错误: {e}", file=sys.stderr)
        return EXIT_INPUT
    except (OSError, Exception) as e:  # noqa: BLE001 —— CLI 顶层兜底，保证退出码可解析
        if args.json:
            print(json.dumps({"ok": False, "status": "io_error",
                              "exit_reason": "io_error",
                              "message": f"{type(e).__name__}: {e}"}, ensure_ascii=False, indent=2))
        else:
            print(f"fw-runner: 执行失败 {type(e).__name__}: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
        return EXIT_IO

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(_human(result))

    if result.status == "interrupted":
        return EXIT_INTERRUPTED
    if result.status in ("needs_human", "stopped", "integration_failed", "needs_confirmation"):
        return EXIT_HUMAN
    if result.status == "complete":
        return EXIT_COMPLETE
    return EXIT_IO


if __name__ == "__main__":
    sys.exit(main())
