"""fw-scaffold CLI：读 task.yaml 一键生成 v2 目录树。

用法:
    python3.11 -m fw_scaffold.cli task.yaml [--output DIR] [--force] [--dry-run] [--json]
    ./bin/fw-scaffold task.yaml

退出码（机器可解析）:
    0 = created/idempotent  目录树已生成（或幂等重跑；输入含验收冲突时也生成，但会在输出中提示）
    1 = task_invalid        输入 task.yaml 未通过 fw-protocol 校验（errors 非空）
    2 = version_mismatch    expected 版本防护：目录已存在且内容/任务书指纹不一致（需 --force 或换目录）
    3 = io/dependency       文件读写失败 / fw-protocol 不可用
    4 = usage               CLI 用法错误
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path
from typing import Optional, Sequence

from .io_utils import ExpectedVersionMismatch
from .scaffold import ScaffoldResult, TaskInvalidError, generate

EXIT_CREATED = 0
EXIT_TASK_INVALID = 1
EXIT_VERSION_MISMATCH = 2
EXIT_IO = 3
EXIT_USAGE = 4
VERSION = "1.0.0"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fw-scaffold",
        description="目录脚手架：读合法 task.yaml（fw-protocol 校验）一键生成 v2 目录树。",
    )
    # argparse 默认用法错误 exit 2；统一为文档契约的 4=usage
    def _usage_error(message: str) -> None:
        raise SystemExit(EXIT_USAGE)
    p.error = _usage_error
    p.add_argument("task_yaml", help="task.yaml 路径（必须通过 fw-protocol 校验）")
    p.add_argument("--output", "-o", default=".", help="生成目录的父目录（默认当前目录）")
    p.add_argument("--force", action="store_true",
                   help="越过 expected 版本防护，覆盖已有目录（重新生成并刷新清单）")
    p.add_argument("--dry-run", action="store_true", help="只打印将生成的目录树，不落盘")
    p.add_argument("--json", action="store_true", help="输出机器可解析 JSON")
    p.add_argument("--version", action="version", version=f"fw-scaffold {VERSION}")
    return p


def _human(result: ScaffoldResult) -> str:
    lines = [
        f"fw-scaffold {VERSION} — 目录脚手架",
        f"任务: {result.task_name}",
        f"根目录: {result.root}",
        f"状态: {result.status}（guard: {result.guard_status}）",
        f"文件数: {len(result.files)}  目录数: {len(result.directories)}",
    ]
    if result.conflicts:
        lines.append("!! 输入任务书含验收冲突（需人工定优先级，见 fw-protocol）——目录结构不受影响，仍已生成:")
        for c in result.conflicts:
            lines.append(f"   !! {c}")
    if result.warnings:
        lines.append("! 校验告警:")
        for w in result.warnings:
            lines.append(f"   ! {w}")
    lines.append("")
    lines.append("生成内容（相对路径）:")
    for rel in result.files:
        lines.append(f"  + {rel}")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        result = generate(args.task_yaml, output_dir=args.output,
                          force=args.force, dry_run=args.dry_run)
    except TaskInvalidError as e:
        r = e.args[0]
        if args.json:
            print(json.dumps({
                "ok": False, "status": "task_invalid",
                "errors": [i.to_dict() for i in r.errors],
                "conflicts": [i.to_dict() for i in r.conflicts],
                "warnings": [i.to_dict() for i in r.warnings],
            }, ensure_ascii=False, indent=2))
        else:
            print(f"fw-scaffold: 任务书校验失败（fw-protocol），拒绝生成。errors={len(r.errors)}",
                  file=sys.stderr)
            for i in r.errors:
                print(f"  (error) {i.code}: {i.message}", file=sys.stderr)
        return EXIT_TASK_INVALID
    except ExpectedVersionMismatch as e:
        if args.json:
            print(json.dumps({"ok": False, "status": "version_mismatch",
                              "message": str(e)}, ensure_ascii=False, indent=2))
        else:
            print(f"fw-scaffold: {e}", file=sys.stderr)
        return EXIT_VERSION_MISMATCH
    except (OSError, Exception) as e:  # noqa: BLE001 —— CLI 顶层兜底，保证退出码可解析
        if args.json:
            print(json.dumps({"ok": False, "status": "io_error",
                              "message": f"{type(e).__name__}: {e}"}, ensure_ascii=False, indent=2))
        else:
            print(f"fw-scaffold: 执行失败 {type(e).__name__}: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
        return EXIT_IO

    if args.dry_run:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2) if args.json
              else _human(result))
        return EXIT_CREATED
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(_human(result))
    return EXIT_CREATED


if __name__ == "__main__":
    sys.exit(main())
