#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fw-spawn.py —— 框架统一的 agent 子进程管理（mac/linux 通用）
用法:
  fw-spawn.py -- <argv...> --out <输出文件> --timeout <秒> --cwd <目录>
  fw-spawn.py <程序> <参数...> --out <输出文件> --timeout <秒> --cwd <目录>
行为:
  1. 用列表参数 + start_new_session=True 起进程（不走 shell，防多行/空格炸）
  2. 输出重定向到 --out（追加写，实时可读）
  3. --timeout 到时杀整个进程组（含孙进程）；收 SIGTERM/SIGINT 时也清理进程组
退出码:
  0  正常结束（含超时/被杀但已清理）
  2  参数错误
"""
import os
import signal
import subprocess
import sys
import time

PROC: "subprocess.Popen | None" = None

def _cleanup(pgid: int) -> None:
    """杀整个进程组；macOS 可能 PermissionError（进程已退出/组不存在）→ 忽略。"""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, PermissionError):
            return
        time.sleep(0.5)

def _handler(signum, frame):
    if PROC is not None and PROC.poll() is None:
        _cleanup(PROC.pid)
    sys.exit(0)

def main() -> int:
    global PROC
    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)

    args = sys.argv[1:]
    argv: list = []
    out: str | None = None
    timeout: float = 300.0
    cwd: str | None = None
    i = 0
    if args and args[0] == "--":
        i = 1   # 支持 fw-spawn.py -- <argv...>
    while i < len(args):
        a = args[i]
        if a == "--out" and i + 1 < len(args):
            out = args[i + 1]; i += 2; continue
        if a == "--timeout" and i + 1 < len(args):
            try:
                timeout = float(args[i + 1])
            except ValueError:
                pass
            i += 2; continue
        if a == "--cwd" and i + 1 < len(args):
            cwd = args[i + 1]; i += 2; continue
        if a == "--":
            argv.extend(args[i + 1:])
            break
        argv.append(a)
        i += 1

    if not argv:
        print("fw-spawn: 缺程序参数（用法: fw-spawn.py <程序> <参数...> --out <文件> [--timeout 秒] [--cwd 目录]）", file=sys.stderr)
        return 2
    if not out:
        print("fw-spawn: 缺 --out", file=sys.stderr)
        return 2

    out_f = open(out, "a", encoding="utf-8")
    proc = subprocess.Popen(
        argv,
        stdout=out_f, stderr=subprocess.STDOUT,
        start_new_session=True,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
    )
    PROC = proc
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rc = proc.poll()
        if rc is not None:
            out_f.close()
            return 0
        time.sleep(1)
    try:
        _cleanup(proc.pid)
    except Exception:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    out_f.close()
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        sys.exit(130)