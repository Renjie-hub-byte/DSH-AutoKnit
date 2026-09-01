#!/usr/bin/env python3
"""code-stats.py —— 源码统计器（统一口径，fw-tools 复用）

统计目录下 .py 文件：总行 / 有效代码行 / 注释行(#) / docstring 行 / 空行，及占比。
- 注释行：tokenize COMMENT token 所在行（# 行内注释也算注释行）
- docstring 行：ast 提取所有字符串 docstring 的 [start,end] 行区间
- 空行：strip 后为空
- 有效行 = 总行 - 注释行 - docstring 行 - 空行
用法: python3 code-stats.py <目录> [<目录> ...]
"""
from __future__ import annotations

import ast
import io
import sys
import tokenize
from pathlib import Path


def stat_file(p: Path) -> dict:
    text = p.read_text(encoding="utf-8")
    lines = text.splitlines()
    total = len(lines) or 1
    blank = sum(1 for ln in lines if not ln.strip())

    comment_lines: set[int] = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                comment_lines.add(tok.start[0])
    except Exception:
        pass  # 解析失败降级（不阻塞整体统计）

    doc_lines: set[int] = set()
    try:
        tree = ast.parse(text)
        for node in ast.walk(tree):
            doc = ast.get_docstring(node, clean=False)
            if doc is None:
                continue
            body = getattr(node, "body", None)
            if not (isinstance(body, list) and body):
                continue
            first = body[0]
            if not (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                continue
            # docstring 行区间：docstring 结束 = first.end_lineno；起始用 first.lineno 回推
            # （Module 无 lineno 属性，用第一个语句的行号）
            start = getattr(node, "lineno", None) or first.lineno
            doc_lines.update(range(start, first.end_lineno + 1))
    except Exception:
        pass

    comment_only = {i for i in comment_lines if i not in doc_lines}
    effective = total - blank - len(doc_lines | comment_lines)
    return {
        "file": str(p),
        "total": total,
        "blank": blank,
        "comment": len(comment_lines),
        "docstring": len(doc_lines),
        "effective": max(effective, 0),
        "comment_ratio": round(len(comment_lines) / total * 100, 1),
        "doc_ratio": round(len(doc_lines) / total * 100, 1),
    }


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    totals = {"total": 0, "blank": 0, "comment": 0, "docstring": 0, "effective": 0}
    n_files = 0
    for arg in sys.argv[1:]:
        d = Path(arg)
        if not d.exists():
            print(f"✗ 路径不存在: {arg}")
            continue
        files = sorted(d.rglob("*.py")) if d.is_dir() else [d]
        for p in files:
            if ".gitkeep" in str(p) or ".venv" in str(p):
                continue
            try:
                s = stat_file(p)
            except Exception as e:
                print(f"✗ 跳过 {p}: {e}")
                continue
            n_files += 1
            for k in totals:
                totals[k] += s[k]
    t = totals["total"] or 1
    print(f"文件数: {n_files}")
    print(f"总行数: {totals['total']}")
    print(f"  有效代码: {totals['effective']}  ({totals['effective']/t*100:.1f}%)")
    print(f"  注释行(#): {totals['comment']}  ({totals['comment']/t*100:.1f}%)")
    print(f"  docstring: {totals['docstring']}  ({totals['docstring']/t*100:.1f}%)")
    print(f"  空行: {totals['blank']}  ({totals['blank']/t*100:.1f}%)")
    print(f"注释含量合计(注释+docstring): {totals['comment']+totals['docstring']}  ({(totals['comment']+totals['docstring'])/t*100:.1f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())