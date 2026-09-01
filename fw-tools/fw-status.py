#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fw-status — 框架运行状态查看器（事件驱动，非轮询）。

用法:
  fw-status <run目录> [--once] [--interval N]

行为:
  1. 读取 快照.json 打印当前整体状态（模块/状态表）
  2. 事件驱动跟随 总日志/dispatch.jsonl：文件有新追加才读取渲染，无新增则阻塞等待
     （内部用 seek 追尾 + 短等待，不是定时轮询全文件——等价 tail -f 语义）
  --once   只看一次当前状态就退出（不跟随）
  --interval N  跟随间隔（秒，默认 1；仅在有阻塞时生效，不产生无意义读取）

示例:
  fw-status 任务-订单管道_2026-08-21          # 跟随实时
  fw-status 任务-订单管道_2026-08-21 --once   # 只看当前
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

# 事件 → 人类可读行（模板）
EVT_LABEL = {
    "scaffold": "🏗️  任务目录已生成",
    "run.start": "▶️  运行开始",
    "module.dispatch": "🚀 派发模块",
    "executor.round.start": "🧑‍💻 executor 开工",
    "executor.round.done": "✅ executor 完成一轮",
    "executor.round.error": "❌ executor 出错",
    "executor.switch": "🔄 换 executor",
    "auditor.round.start": "🔍 auditor 开工",
    "auditor.round": "📋 auditor 判定",
    "module.done": "🎉 模块完成",
    "module.blocked": "⛔ 模块 blocked",
    "module.env_backoff": "⏳ 限流退避重试",
    "module.stuck": "🧱 模块卡死",
    "budget.warn": "⚠️  预算预警",
    "budget.stop": "🛑 预算硬停",
    "integration.check": "🔗 集成验收",
    "run.end": "🏁 运行结束",
}

STATE_ICON = {"done": "✅", "running": "🔄", "blocked": "⛔",
              "needs_human": "🙋", "failed": "❌", "pending": "⬜"}


def load_snapshot(task_dir: Path):
    snap = task_dir / "总日志" / "快照.json"
    if not snap.exists():
        return None
    try:
        return json.loads(snap.read_text(encoding="utf-8"))
    except Exception:
        return None


def render_overview(snap: dict | None) -> None:
    if not snap:
        print("（尚无快照——任务还没开始或还没写检查点）")
        return
    status = snap.get("status", "?")
    print(f"═══ 运行状态: {status} ═══")
    print(f"  任务     : {snap.get('task', '?')}")
    print(f"  run_id   : {snap.get('run_id', '?')}")
    print(f"  已用 token: {snap.get('budget_used_tokens', '?')}")
    mods = snap.get("modules", {})
    if mods:
        print("  模块进度:")
        for mid, st in mods.items():
            icon = STATE_ICON.get(str(st), "⬜")
            print(f"    {icon} {mid}  {st}")
    for mid in snap.get("needs_human", []):
        print(f"  🙋 等待你处理: {mid}")
    print()


def render_event(rec: dict) -> None:
    evt = rec.get("event", "")
    label = EVT_LABEL.get(evt, evt)
    mid = rec.get("module") or rec.get("action") or ""
    detail = rec.get("detail") or {}
    ts = rec.get("ts", "")[11:19] if rec.get("ts") else ""
    line = f"[{ts}] {label}"
    if mid:
        line += f" ({mid})"
    # 关键细节注入
    if evt == "executor.round.start":
        line += f" 轮{detail.get('round')} executor={detail.get('executor_id')}"
    elif evt == "executor.round.done":
        line += (f" 轮{detail.get('round')} 实质进展={detail.get('substance')} "
                 f"tokens={detail.get('tokens')}")
    elif evt == "auditor.round":
        line += (f" 判定={detail.get('verdict')} root={detail.get('root')} "
                 f"置信={detail.get('confidence')}")
    elif evt == "module.blocked":
        line += f" 动作={detail.get('action')} root={detail.get('root')}"
    elif evt == "module.done":
        line += f" root={detail.get('root')} 置信={detail.get('confidence')}"
    elif evt == "module.env_backoff":
        line += f" 等{detail.get('backoff_s'):.0f}s 第{detail.get('attempt')}次"
    elif evt == "budget.warn":
        line += f" 已用{detail.get('budget', {}).get('used')}/{detail.get('budget', {}).get('max_tokens')}"
    elif evt == "executor.round.error":
        line += f" root={detail.get('root')} {detail.get('reason', '')[:60]}"
    print(line, flush=True)


def follow(path: Path, start_seq: int = 0, interval: float = 1.0) -> None:
    """事件驱动跟随：每次从上次位置读新增行；无新增则阻塞等待（非轮询）。"""
    if not path.exists():
        print(f"（事件流尚未创建: {path}）")
        return
    with open(path, "r", encoding="utf-8") as f:
        # seek 到已读进度（首次=末尾，只跟随新事件）
        f.seek(0, os.SEEK_END)
        while True:
            line = f.readline()
            if line:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                render_event(rec)
            else:
                # 无新增 → 阻塞等待（tail -f 语义，不傻轮询）
                time.sleep(interval)


def main() -> int:
    ap = argparse.ArgumentParser(description="框架运行状态查看器（事件驱动）")
    ap.add_argument("task_dir", help="任务目录（含 总日志/ 与 快照.json）")
    ap.add_argument("--once", action="store_true", help="只看当前状态一次")
    ap.add_argument("--interval", type=float, default=1.0, help="跟随等待间隔秒（默认 1）")
    args = ap.parse_args()

    task_dir = Path(args.task_dir)
    if not task_dir.is_dir():
        # 容错：允许传 run 根目录/archived 目录，自动下钻找含 总日志 的目录
        cand = sorted(task_dir.rglob("总日志"), key=lambda p: len(p.parts))
        if cand:
            task_dir = cand[0].parent
        else:
            print(f"✗ 任务目录不存在或找不到 总日志/: {task_dir}", file=sys.stderr)
            return 1

    snap = load_snapshot(task_dir)
    render_overview(snap)

    if args.once:
        # 事件流尾部再补最后几条
        evpath = task_dir / "总日志" / "dispatch.jsonl"
        if evpath.exists():
            print("── 最近事件 ──")
            lines = evpath.read_text(encoding="utf-8").strip().splitlines()
            for line in lines[-6:]:
                if line.strip():
                    try:
                        render_event(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return 0

    print("── 实时事件流（Ctrl+C 退出）──")
    follow(task_dir / "总日志" / "dispatch.jsonl", interval=args.interval)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n（已停止跟随）")
        sys.exit(0)