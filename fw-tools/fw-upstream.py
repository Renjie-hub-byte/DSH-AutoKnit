#!/usr/bin/env python3
"""fw-upstream.py —— 生成模块「上游已实现能力摘要」UPSTREAM.md（0 token，Python AST）

用法: fw-upstream.py <模块目录> [--max-lines N]
产出: <模块目录>/UPSTREAM.md（不存在则创建，覆盖式）

背景（2026-09-01 诊断）：跨模块依赖链（m01→m02→m03）的上游信息此前完全断供——
下游 executor 只能从契约层知道「要对齐什么接口」，不知道「上游已实现哪些可复用能力」，
导致 m03 重写 m02 已有的 summary 聚合（138 行+测试）。本脚本由 fw-executor.sh 在模块
交付后调用（程序侧，0 token），把模块的公开接口/可复用件提取成摘要；下游 executor
启动时由框架注入【前·上游】层，提示「直接复用，禁止重写」。

只提取公开符号（不含 _ 前缀），跳过 __init__/__main__ 等特殊模块；解析失败静默跳过
单个文件（不阻塞）。内容刻意精简（接口名+签名+一句话），不给「文件第几行」这类
引导探索的线索——executor 只需决定「用还是不用」，不需要「去找」。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

MAX_LINES = 200


def _render_sig(fn: ast.FunctionDef) -> str:
    """渲染函数签名（不含函数名，只渲染参数）。"""
    args = fn.args
    parts: list[str] = [a.arg for a in args.args]
    defaults = args.defaults
    if defaults:
        offset = len(args.args) - len(defaults)
        for i, d in enumerate(defaults):
            idx = offset + i
            if idx < len(parts):
                val = ast.unparse(d) if hasattr(ast, "unparse") else "..."
                parts[idx] += f"={val}"
    if args.vararg:
        parts.append(f"*{args.vararg.arg}")
    if args.kwonlyargs:
        parts.extend(a.arg for a in args.kwonlyargs)
    if args.kwarg:
        parts.append(f"**{args.kwarg.arg}")
    return "(" + ", ".join(parts) + ")"


def _docline(node) -> str:
    doc = ast.get_docstring(node)
    if not doc:
        return ""
    first = doc.split("\n")[0].strip()
    return f" — {first[:100]}" if first else ""


def extract(src_dir: Path) -> list[str]:
    """提取 src/ 下公开接口摘要（函数/类签名 + 一句话用途）。"""
    if not src_dir.is_dir():
        return []
    lines: list[str] = []
    py_files = sorted(
        p for p in src_dir.rglob("*.py")
        if p.is_file() and not p.name.startswith("_") and p.name != "__main__.py"
    )
    for fpath in py_files:
        try:
            tree = ast.parse(fpath.read_text(encoding="utf-8"))
        except (SyntaxError, OSError, UnicodeDecodeError):
            continue
        rel = fpath.relative_to(src_dir)
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.FunctionDef):
                if node.name.startswith("_"):
                    continue
                lines.append(f"- `{rel}` → `{node.name}{_render_sig(node)}`{_docline(node)}")
            elif isinstance(node, ast.ClassDef):
                if node.name.startswith("_"):
                    continue
                lines.append(f"- `{rel}` → `class {node.name}`{_docline(node)}")
                for body in node.body:
                    if isinstance(body, ast.FunctionDef) and not body.name.startswith("_"):
                        lines.append(f"  - `.{body.name}{_render_sig(body)}`{_docline(body)}")
    return lines


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: fw-upstream.py <模块目录> [--max-lines N]", file=sys.stderr)
        return 2
    mod_dir = Path(sys.argv[1]).resolve()
    if not mod_dir.is_dir():
        print(f"[fw-upstream] ✗ 模块目录不存在: {mod_dir}", file=sys.stderr)
        return 2
    lines = extract(mod_dir / "src")
    if not lines:
        # 无公开接口也落盘（显式空摘要），下游据此知道「上游无可复用件」，不自己找
        (mod_dir / "UPSTREAM.md").write_text(
            f"# UPSTREAM —— {mod_dir.name}（程序生成）\n\n> 本模块无公开可复用接口（src 下无非 _ 前缀的公开符号，或 src 不存在）。下游无需复用、也无需查找。\n",
            encoding="utf-8",
        )
        print(f"[fw-upstream] ✓ {mod_dir.name}: 无公开接口，已落空摘要")
        return 0
    max_lines = MAX_LINES
    for i, a in enumerate(sys.argv):
        if a == "--max-lines" and i + 1 < len(sys.argv):
            try:
                max_lines = max(10, int(sys.argv[i + 1]))
            except ValueError:
                pass
    body = "\n".join(lines[:max_lines])
    if len(lines) > max_lines:
        body += f"\n…（共 {len(lines)} 条，已截断前 {max_lines} 条）"
    # 探测可 import 的包名/模块名（src 下第一个非 _ 目录 = 包，否则第一个 .py 模块）
    pkg = ""
    src_dir = mod_dir / "src"
    if src_dir.is_dir():
        for d in sorted(src_dir.iterdir()):
            if d.is_dir() and not d.name.startswith("_") and d.name != "__pycache__":
                pkg = d.name
                break
        if not pkg:
            for f in sorted(src_dir.glob("*.py")):
                if not f.name.startswith("_") and f.name != "__main__.py":
                    pkg = f.name[:-3]
                    break
    reuse = (
        "## 下游复用指引（2026-09-01，★ 必须遵守）\n\n"
        "- **只能使用上方「公开接口摘要」列出的接口**。任何上方未列出的能力 = 视为不存在，"
        "禁止自行探索本模块目录或任何其它模块/任务目录（读了会污染你的实现并浪费 token）。\n"
        f"- 复用方式（**直接 import，无需 sys.path——框架已把全部模块 src 加入 PYTHONPATH**）：\n"
        f"  ```python\n"
        f"  from {pkg or '<包名>'} import <你要的接口名>   # 从上方摘要选\n"
        f"  ```\n"
        "- 接口签名/语义以上方摘要为准，**不要读本模块源码**（签名已够用，读了=浪费）。\n"
    )
    text = (
        f"# UPSTREAM —— {mod_dir.name}（程序生成，0 token）\n\n"
        "> 本模块已实现的公开接口/可复用件清单。**下游引用即复用，禁止重写**；\n"
        "> 确实不适用时写明理由，否则 auditor 将按「重复实现」打回。\n\n"
        "## 公开接口摘要\n\n"
        f"{body}\n\n"
        f"{reuse}"
    )
    (mod_dir / "UPSTREAM.md").write_text(text, encoding="utf-8")
    print(f"[fw-upstream] ✓ {mod_dir.name}: 提取 {len(lines)} 条接口 → UPSTREAM.md（含复用指引）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
