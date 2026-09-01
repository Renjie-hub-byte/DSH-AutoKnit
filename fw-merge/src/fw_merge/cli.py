"""Command-line entry point for fw-merge.

Usage
-----
    fw-merge run <task_root> [--output-dir OUT] [--db codegraph.db]
    fw-merge skeleton <task_root> [--db codegraph.db]
    fw-merge conflicts <task_root> [--db codegraph.db]
    fw-merge interfaces <task_root> [--output-dir OUT] [--db codegraph.db]
    fw-merge wiring <task_root> [--output-dir OUT] [--db codegraph.db]
    fw-merge notes <task_root> [--db codegraph.db]
    fw-merge api dsh.merge.<conflicts|skeleton> <task_root> [--db codegraph.db]

The ``run`` subcommand executes the full pipeline (skeleton + conflicts +
compile-readiness notes + interface files + wiring pins).  When the first
argument is a directory rather than a known subcommand it is treated as
``run <task_root>`` for convenience.

Outputs written by ``run`` under the output root::

    <output-root>/
      skeleton.json        # dsh.merge.skeleton payload
      conflicts.json       # dsh.merge.conflicts payload
      compile_notes.json   # non-compilable points with explanations
      wiring.json          # per-module require/import pins (combined)
      interfaces/<module>/interface.json   # per-module target interface
      wiring/<module>/wiring.json          # per-module require/import pins
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from .api import SUPPORTED_APIS, get
from .engine import MergeEngine

#: Known subcommands (dispatch keys). The first CLI arg is matched against this.
_SUBCOMMANDS = ("run", "skeleton", "conflicts", "interfaces", "wiring", "notes", "api")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fw-merge",
        description="程序化合代码 merge：按依赖图把已完成模块按'树枝'归位成合并骨架，"
        "产出接线冲突清单与每模块目标接口文件（纯 python，无 LLM）。",
    )
    parser.add_argument(
        "--db",
        help="codegraph.db 路径；缺省时在 task_root 下自动探测 .codegraph/codegraph.db",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        help="输出目录；缺省为 <task_root>/merge-output",
    )
    parser.add_argument(
        "--indent",
        action="store_true",
        help="JSON 输出缩进（默认紧凑单行）",
    )
    parser.add_argument(
        "args", nargs="*", help="子命令 + 参数；见各子命令"
    )
    return parser


def _dispatch(argv: List[str]) -> int:
    parser = _build_parser()
    ns = parser.parse_args(argv)
    args = list(ns.args)

    indent = 2 if ns.indent else None

    # Convenience: bare `fw-merge <task_root>` == `fw-merge run <task_root>`
    if not args:
        parser.print_help()
        return 0
    if args[0] not in _SUBCOMMANDS:
        args = ["run"] + args

    sub = args[0]
    rest = args[1:]
    db = ns.db

    if sub == "run":
        task_root = _pop_positional(rest, parser, "run <task_root>")
        engine = MergeEngine()
        result = engine.run(task_root, db_path=db, output_root=ns.output_dir)
        _write_run_outputs(result, ns.output_dir, indent)
        _print_run_summary(result)
        return 0

    if sub == "skeleton":
        task_root = _pop_positional(rest, parser, "skeleton <task_root>")
        payload = get("dsh.merge.skeleton", task_root, db_path=db)
        _print_json(payload, indent)
        return 0

    if sub == "conflicts":
        task_root = _pop_positional(rest, parser, "conflicts <task_root>")
        payload = get("dsh.merge.conflicts", task_root, db_path=db)
        _print_json(payload, indent)
        return 0

    if sub == "interfaces":
        task_root = _pop_positional(rest, parser, "interfaces <task_root>")
        engine = MergeEngine()
        result = engine.run(task_root, db_path=db, output_root=ns.output_dir)
        for path in result.interface_files:
            print(path)
        return 0

    if sub == "wiring":
        task_root = _pop_positional(rest, parser, "wiring <task_root>")
        engine = MergeEngine()
        result = engine.run(task_root, db_path=db, output_root=ns.output_dir)
        _print_json(result.wiring_json(), indent)
        return 0

    if sub == "notes":
        task_root = _pop_positional(rest, parser, "notes <task_root>")
        engine = MergeEngine()
        result = engine.run(task_root, db_path=db, output_root=ns.output_dir)
        _print_json(result.compile_notes_json(), indent)
        return 0

    if sub == "api":
        if not rest:
            parser.error("api 需要 <name> <task_root>；name ∈ " + ", ".join(SUPPORTED_APIS))
        api_name = rest[0]
        task_root = rest[1] if len(rest) > 1 else None
        if task_root is None:
            parser.error("api 需要 <task_root>")
        payload = get(api_name, task_root, db_path=db)
        _print_json(payload, indent)
        return 0

    parser.error(f"unknown subcommand: {sub}")
    return 2


def _pop_positional(rest: List[str], parser: argparse.ArgumentParser, what: str) -> str:
    if not rest:
        parser.error(f"缺少参数: {what}")
    return rest[0]


def _write_run_outputs(result, output_dir_arg: Optional[str], indent: Optional[int]) -> None:
    output_root = os.path.abspath(
        output_dir_arg or os.path.join(result.task_root, "merge-output")
    )
    os.makedirs(output_root, exist_ok=True)
    with open(os.path.join(output_root, "skeleton.json"), "w", encoding="utf-8") as fh:
        json.dump(result.skeleton_json(), fh, ensure_ascii=False, indent=indent or 2)
        fh.write("\n")
    with open(os.path.join(output_root, "conflicts.json"), "w", encoding="utf-8") as fh:
        json.dump(result.conflicts_json(), fh, ensure_ascii=False, indent=indent or 2)
        fh.write("\n")
    with open(os.path.join(output_root, "compile_notes.json"), "w", encoding="utf-8") as fh:
        json.dump(result.compile_notes_json(), fh, ensure_ascii=False, indent=indent or 2)
        fh.write("\n")
    with open(os.path.join(output_root, "wiring.json"), "w", encoding="utf-8") as fh:
        json.dump(result.wiring_json(), fh, ensure_ascii=False, indent=indent or 2)
        fh.write("\n")


def _print_json(payload, indent: Optional[int]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=indent))


def _print_run_summary(result) -> None:
    mods = sorted(m.id for m in result.modules)
    print(f"task_root     : {result.task_root}")
    print(f"db            : {result.db_path or '(无 codegraph.db, 用 contract 依赖)'}")
    print(f"modules       : {', '.join(mods) if mods else '(无)'}")
    print(f"skeleton 条目  : {len(result.plan.entries())}")
    print(f"conflicts 条目 : {len(result.conflicts)}")
    print(f"compile notes  : {len(result.compile_notes)}")
    print(f"interface 文件 : {len(result.interface_files)}")
    print(f"wiring 文件    : {len(result.wiring_files)}")
    for w in result.warnings:
        print(f"[warn] {w}")


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry; returns process exit code."""
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        return _dispatch(argv)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # surface a clean error + non-zero exit
        print(f"fw-merge: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
