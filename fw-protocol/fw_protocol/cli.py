"""fw-protocol CLI：校验 task.yaml，返回结构化结果。

用法:
    python3.11 -m fw_protocol.cli [选项] task.yaml
    python3.11 -m fw_protocol.cli task.yaml --json

退出码（机器可解析）:
    0 = pass         通过（无错误、无冲突、无告警）
    1 = error        校验失败（结构错误 / 依赖环 / 接口重复 / 预算矛盾）
    2 = conflict     验收冲突，需人工定优先级
    3 = io/schema    文件读取或 schema 加载失败
    4 = usage        CLI 用法错误
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from typing import Any, Dict, Optional, Sequence

from .io_utils import TaskYamlError, read_task_document
from .model import ValidationResult
from .schema import load_schema
from .validate import validate_document

EXIT_PASS = 0
EXIT_ERROR = 1
EXIT_CONFLICT = 2
EXIT_IO = 3
EXIT_USAGE = 4

VERSION = "1.0.0"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fw-protocol",
        description="任务书协议校验器：结构（JSON Schema）+ 依赖环/接口重复/验收冲突三查。",
    )
    p.add_argument("task_yaml", help="task.yaml 路径")
    p.add_argument("--schema", default=None, help="自定义 JSON Schema 路径（默认用包内置）")
    p.add_argument("--json", action="store_true", help="输出机器可解析 JSON（含 effective 默认值补全后的任务书）")
    p.add_argument("--no-cycle", action="store_true", help="关闭依赖环检测")
    p.add_argument("--no-interface", action="store_true", help="关闭接口重复检测")
    p.add_argument("--no-conflict", action="store_true", help="关闭验收冲突检测")
    p.add_argument("--effective", metavar="FILE", default=None,
                   help="把默认值补全后的任务书写到 FILE（供下游模块消费）")
    p.add_argument("--version", action="version", version=f"fw-protocol {VERSION}")
    return p


def _format_human(result: ValidationResult, path: str) -> str:
    eff = result.effective if isinstance(result.effective, dict) else {}
    name = (eff.get("task") or {}).get("name", "?") if isinstance(eff.get("task"), dict) else "?"
    lines = [
        f"fw-protocol {VERSION} — 任务书校验",
        f"文件: {path}  任务: {name}  状态: {result.status.upper()}",
        f"  errors={len(result.errors)}  conflicts={len(result.conflicts)}  warnings={len(result.warnings)}",
    ]
    for issue in result.all_issues:
        where = f"[{issue.module_id}] " if issue.module_id else ""
        lines.append(f"  ({issue.severity}) {issue.code}: {where}{issue.message}")
        if issue.code == "dep_cycle":
            lines.append(f"      环路径: {' -> '.join(issue.detail.get('cycle', []))}")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        schema = load_schema(args.schema) if args.schema else None
        doc = read_task_document(args.task_yaml)
    except TaskYamlError as e:
        print(f"fw-protocol: 读取失败: {e}", file=sys.stderr)
        return EXIT_IO
    except (OSError, json.JSONDecodeError) as e:
        print(f"fw-protocol: schema 读取失败: {e}", file=sys.stderr)
        return EXIT_IO

    if args.no_cycle or args.no_interface or args.no_conflict:
        # 在任务书副本上落关开关后重新校验（不污染原始文件）
        doc = copy.deepcopy(doc)
        integ = doc.setdefault("integration", {})
        check = integ.setdefault("check", {})
        if args.no_cycle:
            check["dependency_cycle"] = False
        if args.no_interface:
            check["interface_duplicate"] = False
        if args.no_conflict:
            check["acceptance_conflict"] = False

    result = validate_document(doc, schema=schema)

    if args.effective:
        with open(args.effective, "w", encoding="utf-8") as f:
            json.dump(result.effective, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(f"effective 任务书已写入: {args.effective}")

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(_format_human(result, args.task_yaml))

    if result.status == "error":
        return EXIT_ERROR
    if result.status == "conflict":
        return EXIT_CONFLICT
    return EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
