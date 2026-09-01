#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fw-dashboard.py —— 数据桥面板数据：:8765 健康 + 最近 run 一览。

数据桥（fw-api serve）是 dashboard 的数据源：run 注册表 + 每 run 的
token 消耗（输入/输出/缓存命中）已在 registry.py / usage.py 接好。
本命令只读聚合展示：
  1. 数据桥连通性（未运行则提示怎么拉起）
  2. 最近 N 个 run 的状态 + token 明细（计费 = 未缓存输入 + 输出）

用法:
  fw-dashboard [--limit N]    # 最近 N 个 run（默认 10）
  fw-dashboard --json         # 机器可读
"""
import json
import sys
import urllib.request
from datetime import datetime

BRIDGE = "http://127.0.0.1:8765"


def _get(path: str) -> dict:
    with urllib.request.urlopen(BRIDGE + path, timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> None:
    limit = 10
    as_json = False
    args = sys.argv[1:]
    if "--json" in args:
        as_json = True
        args.remove("--json")
    if "--limit" in args:
        i = args.index("--limit")
        if i + 1 < len(args):
            limit = max(1, min(int(args[i + 1]), 50))
            del args[i:i + 2]

    try:
        runs = _get("/api/runs")
    except Exception as e:
        msg = (f"数据桥未运行（{type(e).__name__}）。拉起方式："
               f"launchctl kickstart -k gui/{__import__('os').getuid()}/com.autoknit.fwapi-bridge"
               f"，或直接 autoknit run（会自动拉起）。")
        if as_json:
            print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
        else:
            print("❌ " + msg)
        sys.exit(1)

    # 最近 limit 个，附 token 明细（run_usage 每个都查一次，失败降级）
    rows = []
    for r in runs[-limit:][::-1]:
        rid = r["run_id"]
        usage = {}
        try:
            usage = _get(f"/api/runs/{rid}/usage").get("run", {})
        except Exception:
            pass
        rows.append({
            "run_id": rid[:26],
            "task": r.get("task", ""),
            "status": r.get("status", ""),
            "input": usage.get("input", 0),
            "output": usage.get("output", 0),
            "cache": usage.get("cache_read", 0),
            "billable": usage.get("input", 0) + usage.get("output", 0),
        })

    if as_json:
        print(json.dumps({"ok": True, "bridge": "up", "runs": rows}, ensure_ascii=False))
        return

    print("AutoKnit dashboard —— 数据桥 :8765 在线")
    print("-" * 78)
    if not rows:
        print("（暂无 run）")
        return
    print(f"{'run_id':<28} {'任务':<22} {'状态':<9} {'计费token':>10} {'缓存读':>10}")
    print("-" * 78)
    for x in rows:
        print(f"{x['run_id']:<28} {(x['task'] or '')[:20]:<22} {x['status']:<9} "
              f"{x['billable']:>10,} {x['cache']:>10,}")
    print("-" * 78)
    print("计费 = 未缓存输入 + 输出（缓存读单列，不计费）。明细: autoknit token <关键字>")


if __name__ == "__main__":
    main()
