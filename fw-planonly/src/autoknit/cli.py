"""autoknit CLI 入口。

注册三个子命令：
- ``autoknit plan-only <dir>``：只跑 planner，产出 task.yaml、写 checkpoint、打印摘要，
  规划完即停退出码 0；
- ``autoknit summary <dir>``：对外只读接口 ``dsh.plan-only.summary`` 的命令行对应，
  缺 task.yaml 时确定性报错；
- ``autoknit run <dir> --resume-from-checkpoint``：用同一份 task.yaml 接续已规划任务，
  识别 plan checkpoint、不重复规划。
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from .errors import AutoknitError
from .resume import resume_from_checkpoint
from .runner import run_plan_only
from .summary import format_summary_text, load_plan_summary

PROG = "autoknit"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=PROG, description="autoknit —— plan-only 模式（只跑规划，给真人审）")
    parser.add_argument("--version", action="version", version=f"{PROG} 0.1.0")
    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    p_plan = sub.add_parser("plan-only", help="只跑 planner：产出 task.yaml、写 checkpoint、打印摘要，规划完即停")
    p_plan.add_argument("dir", metavar="<dir>", help="任务目录（须含 PRD，如 PRD.md / 任务-*.md）")
    p_plan.add_argument("--prd", metavar="<path>", help="显式指定 PRD 文件路径（缺省自动探测）")
    p_plan.set_defaults(handler=_cmd_plan_only)

    p_sum = sub.add_parser("summary", help="读取已有 task.yaml 并打印规划摘要（dsh.plan-only.summary）")
    p_sum.add_argument("dir", metavar="<dir>", help="任务目录（须已含 task.yaml）")
    p_sum.set_defaults(handler=_cmd_summary)

    p_run = sub.add_parser("run", help="接续已规划任务（须先跑过 plan-only）")
    p_run.add_argument("dir", metavar="<dir>", help="任务目录（须已含 plan checkpoint + task.yaml）")
    p_run.add_argument("--resume-from-checkpoint", action="store_true",
                       help="从 plan checkpoint 接续，用同一 task.yaml、不重复规划（plan-only 模式必填）")
    p_run.set_defaults(handler=_cmd_run)

    return parser


def _cmd_run(args: argparse.Namespace) -> int:
    if not args.resume_from_checkpoint:
        print(
            f"{PROG}: error: plan-only 模式的 run 只支持接续已规划任务，"
            "请传 --resume-from-checkpoint 从 checkpoint 接续（不重复规划）",
            file=sys.stderr,
        )
        return 2
    result = resume_from_checkpoint(args.dir)
    print(f"run 已接续：识别 plan checkpoint（planned=True, stage=idle）")
    print(f"  task.yaml: {result.task_yaml_path}")
    print(f"  模块数: {len(result.plan.modules)}，执行顺序: {', '.join(result.plan.execution_order) or '(空)'}")
    print("run 接续完成：复用同一 task.yaml，未重复规划，退出码 0（未执行/审计/拆分，未发 LLM 请求）")
    return 0


def _cmd_plan_only(args: argparse.Namespace) -> int:
    result = run_plan_only(args.dir, prd_override=args.prd)
    print(format_summary_text(result.summary))
    print(f"task.yaml 已写入: {result.task_yaml_path}")
    print(f"checkpoint 已写入: {', '.join(str(p) for p in result.checkpoint_paths)}")
    print("plan-only 完成：规划完即停，退出码 0（未执行/审计/拆分，未发任何 LLM 请求）")
    return 0


def _cmd_summary(args: argparse.Namespace) -> int:
    summary = load_plan_summary(args.dir)
    print(format_summary_text(summary))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except AutoknitError as exc:
        print(f"{PROG}: error: {exc}", file=sys.stderr)
        return exc.exit_code


if __name__ == "__main__":
    sys.exit(main())
