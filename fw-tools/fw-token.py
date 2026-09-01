#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fw-token.py —— 查看 framework 任务的 token 消耗明细（输入/输出/缓存命中）
用法:
  fw-token [会话目录前缀]     # 默认统计所有 framework 相关会话
  fw-token 任务名             # 只统计含该关键字的会话
  fw-token --json [--since 毫秒时间戳]   # 机器可读（脚本回填用）：输出合计 JSON

数据来源：$DSH_HOME/sessions/*/session-*/session.jsonl.zstd 里 provider 上报的 usage
（fw 专用 DSH_HOME 默认 ~/.fw-dsh；兼容旧 ~/.dsh）。
framework 快照里的 budget_used_tokens 恒 0（统计未接回），本工具从会话文件读真实消耗。
BUG-004 修复（2026-08-25）：sess_root 改从 DSH_HOME 环境变量推断；新增 --json/--since 供
fw-executor.sh 回填真实 token。
"""
import json, os, subprocess, glob, sys, re

def _sess_root():
    """会话根（与 fw 全链路统一）：$FW_DSH_HOME/sessions → $DSH_HOME/sessions
    → ~/.fw-dsh/sessions → ~/.dsh/sessions。

    fw 的源变量是 FW_DSH_HOME；FW_DSH_HOME 隔离环境下数据桥/记账须读对会话，
    故以它最高优先（历史只读 DSH_HOME，隔离环境会回退到 ~/.fw-dsh 读错）。
    """
    for base in (os.environ.get("FW_DSH_HOME"), os.environ.get("DSH_HOME"), os.path.expanduser("~/.fw-dsh"), os.path.expanduser("~/.dsh")):
        if base:
            p = os.path.join(base, "sessions")
            if os.path.isdir(p):
                return p
    return os.path.join(os.path.expanduser("~/.dsh"), "sessions")


def _sess_roots():
    """全部需要扫描的会话根（去重保序）：当前生效根 + 历史根。

    历史上 fw 曾用 ~/.fw-dsh 与 ~/.fw-dsh-bench 两个根跑 run（bench 隔离环境），
    数据桥按当前 env 只认一个根会漏聚合另一根里的会话（BUG-009 配套）。
    规则：
    - 显式设置了 FW_DSH_HOME/DSH_HOME 且指向**默认候选之一**时 → 补扫其它默认根
      （fwapi 用 ~/.fw-dsh，但历史 run 散落在 ~/.fw-dsh-bench，两处都要聚合）；
    - 显式路径是**自定义目录**（如测试隔离的 tmp_path）→ 严格只扫该路径，
      不串入真实默认根（保持单根隔离语义）；
    - 未显式设置 → 扫全部存在的默认候选。
    """
    defaults = tuple(
        os.path.normpath(os.path.expanduser(p))
        for p in ("~/.fw-dsh", "~/.fw-dsh-bench", "~/.dsh")
    )
    explicit = [os.environ.get("FW_DSH_HOME"), os.environ.get("DSH_HOME")]
    explicit = [os.path.normpath(p) for p in explicit if p]

    def _add(sp, seen, roots):
        sp = os.path.normpath(sp)
        if sp not in seen and os.path.isdir(sp):
            seen.add(sp)
            roots.append(sp)

    seen: set = set()
    roots: list = []
    if explicit:
        for p in explicit:
            _add(os.path.join(p, "sessions"), seen, roots)
        # 显式路径均为默认候选 → 补扫其它默认根（历史 run 可能散落在另一根）
        if all(p in defaults for p in explicit):
            for d in defaults:
                _add(os.path.join(d, "sessions"), seen, roots)
    else:
        for d in defaults:
            _add(os.path.join(d, "sessions"), seen, roots)
    return roots or [os.path.normpath(os.path.join(os.path.expanduser("~/.dsh"), "sessions"))]

def _decode_dirname(name):
    """解码 DSH 会话目录名：非 ASCII 码点编码为 ~HHHH（UTF-16 hex）、/ 编码为 -。

    会话目录名 = "--" + 会话 cwd 的编码路径 + "--"。解码回可读路径，
    供按任务名 / 模块 id 精确匹配（BUG-006）。
    """
    out, i, n = [], 0, len(name)
    while i < n:
        ch = name[i]
        if ch == "~" and i + 5 <= n and all(c in "0123456789abcdefABCDEF" for c in name[i+1:i+5]):
            try:
                out.append(chr(int(name[i+1:i+5], 16)))
                i += 5
                continue
            except (ValueError, OverflowError):
                pass
        out.append(ch)
        i += 1
    return "".join(out)

_MODULE_ID_RE = re.compile(r"^m\d+$")

def _match_dirname(decoded_dirname, kw):
    """会话目录匹配规则（BUG-006 修复 2026-08-28；BUG-007 修复 2026-08-31）：
    - 模块 id（m\\d+）→ 严格段匹配：紧跟 ``modules`` 段之后的那一段 == kw 或 kw- 前缀
      （模块会话目录形如 ``.../modules/mXX-名称``，路径分隔符 / 在目录名中编码为 -）。
      BUG-007：此前"任意段 == kw"会命中任务目录名里的 mXX（如 bench-autoknit-m02 /
      dsh_cockpit_m02），导致 m01/m02 聚合出完全相同的数——须锚定到 modules 段之后。
    - 任务名 / 其它关键字 → 解码后子串（兼容中文任务名）。
    - 默认 framework → 保持子串兼容。
    """
    if kw == "framework":
        return kw in decoded_dirname
    if _MODULE_ID_RE.match(kw):
        segs = re.split(r"[-/]", decoded_dirname)
        for i, seg in enumerate(segs[:-1]):
            if seg == "modules" and (segs[i + 1] == kw or segs[i + 1].startswith(kw + "-")):
                return True
        return False
    return kw in decoded_dirname

def _align_since(since_ms, sample_t):
    """把 since_ms 归一化到与样本 time 相同的单位。

    dsh 会话 jsonl 的 time 是毫秒级（13 位）；调用方（fw-executor.sh /
    fw-auditor.sh）用 `stat -f %m` 传的是秒级（10 位）。不归一化会导致
    `t >= since_ms` 永远成立、--since 过滤失效（BUG-004 隐藏缺陷）。
    """
    if since_ms <= 0 or not sample_t:
        return since_ms
    if len(str(int(sample_t))) >= 12 and len(str(int(since_ms))) <= 10:
        return since_ms * 1000
    return since_ms

def get_usage(f, since_ms=0):
    r = subprocess.run(["zstd", "-dc", f], capture_output=True)
    txt = r.stdout.decode("utf-8", errors="replace")
    calls = []
    sample_t = 0
    for line in txt.splitlines():
        if '"usage"' not in line:
            continue
        try:
            d = json.loads(line)
            u = (d.get("data") or {}).get("chunk") or {}
            usage = u.get("usage")
            if not isinstance(usage, dict):
                continue
            # dsh 会话格式兼容（2026-08-28）：新版 assistant/chunk 的 usage 嵌套两层
            #   {"type":"usage","usage":{"inputTokens":...}} ；旧版直接 {"inputTokens":...}
            if "usage" in usage and isinstance(usage["usage"], dict):
                usage = usage["usage"]
            t = d.get("time", 0)
            if not sample_t and t:
                sample_t = t
            if t >= _align_since(since_ms, sample_t):
                calls.append(usage)
        except Exception:
            continue
    return calls

def main():
    args = sys.argv[1:]
    as_json = False
    since_ms = 0
    if "--json" in args:
        as_json = True
        args.remove("--json")
    if "--since" in args:
        i = args.index("--since")
        if i + 1 < len(args):
            try:
                since_ms = int(args[i + 1])
            except ValueError:
                since_ms = 0
            del args[i:i + 2]
    cwd_kw = None
    if "--cwd" in args:
        i = args.index("--cwd")
        if i + 1 < len(args):
            cwd_kw = args[i + 1]
            del args[i:i + 2]
    kw = args[0] if args else "framework"
    kw_decoded = _decode_dirname(kw)
    roots = _sess_roots()
    tot = {"i": 0, "o": 0, "c": 0, "n": 0}
    rows = []
    for sess_root in roots:
        for d in sorted(glob.glob(sess_root + "/*/session-*/session.jsonl.zstd")):
            # 会话目录名 = "--" + 会话 cwd 编码路径 + "--"（d 形如
            # <root>/<cwd编码目录>/session-<id>/session.jsonl.zstd）
            dirname = d.split("sessions/")[1].split("/session-")[0]
            decoded = _decode_dirname(dirname)
            # --cwd 限定：会话 cwd 必须落在该 run 的任务目录下（模块会话形如
            # <task_dir>.../modules/mXX-...）。防止跨 run 同名模块（m01/m02/m03
            # 是通用 id）串聚合（BUG-009 修复 2026-08-31）。
            if cwd_kw:
                cwd_enc = cwd_kw.replace("/", "-").strip("-")
                if cwd_enc not in decoded:
                    continue
            if not _match_dirname(decoded, kw_decoded):
                continue
            cs = get_usage(d, since_ms=since_ms)
            if not cs:
                continue
            i = sum(x.get("inputTokens", 0) for x in cs)
            o = sum(x.get("outputTokens", 0) for x in cs)
            ca = sum(x.get("cacheReadTokens", 0) for x in cs)
            key = d.split("sessions/")[1].split("/session-")[0]
            rows.append((key, i, o, ca, len(cs)))
            tot["i"] += i; tot["o"] += o; tot["c"] += ca; tot["n"] += len(cs)
    if as_json:
        # 机器可读：计费 = 输入+输出（缓存命中不计费，单独上报）
        print(json.dumps({
            "input_tokens": tot["i"], "output_tokens": tot["o"],
            "cache_read_tokens": tot["c"], "calls": tot["n"],
            "billable_tokens": tot["i"] + tot["o"],
            "sessions": len(rows),
        }, ensure_ascii=False))
        return
    if not rows:
        print(f"没找到含 '{kw}' 的会话（{sess_root}）")
        return
    print(f"{'会话':<50} {'输入':>9} {'输出':>9} {'缓存':>10} {'调用':>5}")
    print("-" * 90)
    for k, i, o, c, n in sorted(rows, key=lambda x: -x[1])[:20]:
        print(f"{k[:50]:<50} {i:>9,} {o:>9,} {c:>10,} {n:>5}")
    print("-" * 90)
    bill = tot["i"] + tot["o"]
    print(f"合计({len(rows)} 会话)  输入: {tot['i']:,}  输出: {tot['o']:,}  计费: {bill:,}")
    print(f"缓存命中: {tot['c']:,}（占 {tot['c']/(tot['i']+tot['c'])*100:.1f}%）")
    print(f"平均每次调用: 输入 {tot['i']//max(tot['n'],1):,} / 输出 {tot['o']//max(tot['n'],1):,}")

if __name__ == "__main__":
    main()