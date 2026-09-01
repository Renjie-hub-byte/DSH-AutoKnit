#!/usr/bin/env python3
"""fw-trace.py —— executor 会话事件流 → 客观事实清单（audit-facts）。

程序采集（0 token，不进模型上下文前不花钱）：解压 executor 本轮会话
session.jsonl.zstd，抽 tool/call + tool/result 按 callId 配对，产出紧凑的
客观事实清单，供 auditor 对照验收——替代 executor 自述（交付说明.md）。

这是全景文档「宿主服务（模型碰不到，只给程序用）：dsh-session-query/filterEvents
→ 加工成最小事实喂角色」的简化落地（EXEC_TRACE 的升级版：json 解析 + result 配对）。

用法：
    fw-trace.py --mark <TRACE_MARK路径> --out <输出md> [--session-dir <sessions路径>]
退出码：0=生成成功；1=无会话/无事实（输出为空清单）。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def _sess_root(session_dir: str | None) -> str:
    for base in (session_dir, os.environ.get("DSH_HOME"),
                 os.path.expanduser("~/.fw-dsh"), os.path.expanduser("~/.dsh")):
        if base:
            p = os.path.join(base, "sessions")
            if os.path.isdir(p):
                return p
    return os.path.join(os.path.expanduser("~/.fw-dsh"), "sessions")


def _find_new_sessions(sess_root: str, mark: str) -> list[str]:
    mark_t = 0.0
    if mark and os.path.exists(mark):
        try:
            mark_t = os.path.getmtime(mark)
        except OSError:
            mark_t = 0.0
    found: list[str] = []
    for root, _dirs, files in os.walk(sess_root):
        for f in files:
            if f.startswith("session") and f.endswith(".jsonl.zstd"):
                p = os.path.join(root, f)
                try:
                    if os.path.getmtime(p) >= mark_t:
                        found.append(p)
                except OSError:
                    pass
    return sorted(found)


def _decode_events(path: str):
    """逐行 json.loads 解压会话，yield (type, data)。"""
    r = subprocess.run(["zstd", "-dc", path], capture_output=True, timeout=120)
    txt = r.stdout.decode("utf-8", errors="replace")
    for ln in txt.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            o = json.loads(ln)
        except Exception:
            continue
        yield o.get("type"), o.get("data")


def _call_arguments(data: dict) -> dict:
    """tool/call 的 arguments 是 JSON 字符串，解析成 dict（失败给原文）。"""
    args = data.get("arguments")
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        try:
            return json.loads(args)
        except Exception:
            return {"_raw": args}
    return {}


def _result_text(data: dict) -> tuple[str, bool]:
    """tool/result 的文本 + 是否出错。"""
    msg = data.get("message") or {}
    parts: list[str] = []
    is_err = False
    for c in msg.get("content") or []:
        if not isinstance(c, dict):
            continue
        if c.get("isError"):
            is_err = True
        for inner in c.get("content") or []:
            if isinstance(inner, dict) and inner.get("type") == "text":
                t = inner.get("text") or ""
                if t:
                    parts.append(t)
    if data.get("error"):
        is_err = True
    return "\n".join(parts), is_err


def collect(sess_root: str, mark: str) -> dict:
    """采集客观事实。返回 {'calls': {callId: {name, args, desc}},
    'results': {callId: {text, is_err}}}。"""
    calls: dict = {}
    results: dict = {}
    for p in _find_new_sessions(sess_root, mark):
        for typ, data in _decode_events(p):
            if typ == "tool/call" and isinstance(data, dict):
                cid = data.get("callId") or ""
                calls[cid] = {
                    "name": data.get("name") or "?",
                    "args": _call_arguments(data),
                    "desc": data.get("description") or "",
                }
            elif typ == "tool/result" and isinstance(data, dict):
                msg = data.get("message") or {}
                cid = (msg.get("source") or {}).get("callId") or ""
                text, is_err = _result_text(data)
                if cid:
                    results[cid] = {"text": text, "is_err": is_err}
    return {"calls": calls, "results": results}


def _trunc(s: str, n: int = 240) -> str:
    s = (s or "").strip().replace("\n", "⏎")
    return s if len(s) <= n else s[:n] + "…"


def _overbound_paths(calls: dict, cwd: str | None) -> list[str]:
    """越界检测（程序侧，0 成本）：write/edit 的文件是否越出模块目录。"""
    if not cwd:
        return []
    cwd_abs = os.path.abspath(cwd)
    over: list[str] = []
    for c in calls.values():
        if c["name"] not in ("write", "edit", "str_replace_editor"):
            continue
        path = str(c["args"].get("path") or c["args"].get("file_path") or "").strip()
        if not path:
            continue
        abs_p = os.path.abspath(path) if os.path.isabs(path) else os.path.abspath(os.path.join(cwd, path))
        if not (abs_p == cwd_abs or abs_p.startswith(cwd_abs + os.sep)):
            over.append(path)
    return over


def _semgrep_findings(cwd: str | None) -> list[str]:
    """semgrep 扫 src/ 模式 bug（程序侧，约 10s）。"""
    if not cwd:
        return []
    src = os.path.join(cwd, "src")
    if not os.path.isdir(src):
        return []
    semgrep = os.path.expanduser("~/.local/bin/semgrep")
    if not os.path.exists(semgrep):
        return []
    try:
        r = subprocess.run([semgrep, "scan", "--config", "auto", "--json", src],
                           capture_output=True, text=True, timeout=120)
        d = json.loads(r.stdout or "{}")
        res = d.get("results") or []
        out = []
        for x in res[:15]:
            cid = str(x.get("check_id", "?"))
            path = str(x.get("path", "?"))
            line = (x.get("start") or {}).get("line", "?")
            out.append(f"1. `{cid}` @ {path.split('/')[-1]}:{line}")
        return out
    except Exception:
        return []


def render(facts: dict, n_sessions: int, overbound: list[str] | None = None,
           semgrep: list[str] | None = None, cwd: str | None = None) -> str:
    calls: dict = facts["calls"]
    results: dict = facts["results"]

    def _rel(p: str) -> str:
        """绝对路径相对 cwd 显示（压缩长度，信息不丢：auditor 只关心写了哪些文件）。"""
        if cwd and p:
            try:
                cwd_abs = os.path.abspath(cwd)
                abs_p = os.path.abspath(os.path.expanduser(p))
                if abs_p == cwd_abs:
                    return "."
                if abs_p.startswith(cwd_abs + os.sep):
                    return os.path.relpath(abs_p, cwd_abs)
            except Exception:
                pass
        return p

    lines = ["# 客观事实清单（程序从 executor 会话事件流采集，非 executor 自述）", ""]
    lines.append(f"工具调用 {len(calls)} 次（来自 {n_sessions} 个会话）。", )
    lines.append("")

    bash, writes, edits, reads, tests, others = [], [], [], [], [], []
    for cid, c in calls.items():
        name = c["name"]
        args = c["args"]
        r = results.get(cid, {})
        rtext = _trunc(r.get("text", ""), 200)
        rmark = " ✗" if r.get("is_err") else ""
        if name == "bash":
            cmd = _trunc(str(args.get("command") or args.get("_raw") or ""), 120)
            bash.append(f"`{cmd}`{rmark}")
            if "pytest" in cmd or "test/" in cmd or "unittest" in cmd or ".venv/bin/python" in cmd:
                tests.append(f"`{cmd}`{rmark}" + (f" → {rtext}" if rtext else ""))
        elif name in ("write", "str_replace_editor"):
            writes.append(f"`{_rel(str(args.get('path') or args.get('file_path') or '?'))}`")
        elif name == "edit":
            edits.append(f"`{_rel(str(args.get('path') or args.get('file_path') or '?'))}`")
        elif name == "read":
            reads.append(f"`{_rel(str(args.get('path') or args.get('file_path') or '?'))}`")
        else:
            others.append(f"`{name}` {_trunc(json.dumps(args, ensure_ascii=False), 80)}")

    def _sec(title, items, cap=12):
        if not items:
            return
        lines.append(f"## {title}（{len(items)}）")
        lines.extend(items[:cap])
        if len(items) > cap:
            lines.append(f"- … 其余 {len(items) - cap} 条略")
        lines.append("")

    _sec("写入/修改的文件（write/edit）", writes + edits)
    _sec("读取的文件（read）", reads, 8)
    _sec("执行的命令（bash）", bash, 10)
    _sec("其他工具", others, 6)
    # 越界检测（程序侧，block 依据）
    lines.append("## 越界检测（程序侧：write/edit 是否越出本模块目录）")
    if overbound:
        for p in overbound:
            lines.append(f"1. `{p}` ← ⚠️ 越出模块目录，可能改到别的模块")
    else:
        lines.append("- 未发现 write/edit 越出本模块目录")
    lines.append("")
    # semgrep 模式 bug（程序侧）
    lines.append("## 模式 bug 扫描（程序侧 semgrep）")
    if semgrep:
        lines.extend(semgrep)
    else:
        lines.append("- 未发现模式 bug（或 semgrep 不可用/无 src）")
    lines.append("")
    # 测试运行 = bash 里跑 pytest/python test 的命令（含结果尾部，auditor 验收证据）
    if tests:
        lines.append("## 测试运行（含结果尾部，验收证据）")
        lines.extend(tests[:8])
        if len(tests) > 8:
            lines.append(f"- … 其余 {len(tests) - 8} 条略")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="executor 会话 → 客观事实清单")
    ap.add_argument("--mark", default="", help="TRACE_MARK 路径（mtime 作为本轮会话起点）")
    ap.add_argument("--out", required=True, help="输出 md 路径")
    ap.add_argument("--session-dir", default=None, help="sessions 根目录（默认 DSH_HOME/.fw-dsh）")
    ap.add_argument("--cwd", default=None, help="模块目录（越界检测边界 + semgrep 扫描范围）")
    ap.add_argument("--max-bytes", type=int, default=3000, help="输出上限字节（auditor 瘦身）")
    args = ap.parse_args()

    sess_root = _sess_root(args.session_dir)
    if not os.path.isdir(sess_root):
        print(f"[fw-trace] 无会话目录 {sess_root}，输出空清单")
        Path(args.out).write_text("# 客观事实清单\n\n（未发现 executor 会话）\n", encoding="utf-8")
        return 0
    new = _find_new_sessions(sess_root, args.mark)
    facts = collect(sess_root, args.mark)
    overbound = _overbound_paths(facts["calls"], args.cwd)
    semgrep = _semgrep_findings(args.cwd)
    text = render(facts, len(new), overbound, semgrep, args.cwd)
    # 硬上限：auditor 瘦身——事实清单本身也要有界
    if len(text.encode("utf-8")) > args.max_bytes:
        text = text[:args.max_bytes] + "\n…（客观事实已截断，详情见会话文件）\n"
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(f"[fw-trace] 客观事实已生成（{len(facts['calls'])} 次工具调用，{len(text)} 字符 → {args.out}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
