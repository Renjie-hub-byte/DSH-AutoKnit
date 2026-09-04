"""AutoKnit config_cli —— 供 fw-run.sh 在 shell 层读 dflow.yaml 并透传。

子命令（均无副作用，纯打印）:
  dump-env --cwd <任务目录> [--config <dflow.yaml>]
      → JSON {FW_EXECUTOR_MODEL:..., FW_EXECUTOR_REASONING:...}（模型 env，供 shell setenv）
  run-flags --cwd <任务目录> [--config <dflow.yaml>]
      → 空格分隔的 CLI flag（--split-exit-threshold 800 --max-parallel 2 ...），喂给 fw_runner.cli
好处：阈值/并行/模型/思考模式统一收进 dflow.yaml，shell 侧只做 JSON/flag 透传，逻辑在 config.py。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import RUNTIME_KEYS, resolve_combined

# runtime 键 → runner CLI flag 名（仅支持 runner cli 已暴露的）
_RUNTIME_TO_FLAG = {
    "max_parallel": "--max-parallel",
    "executor_max_rounds": "--executor-max-rounds",
    "retry_before_switch": "--retry-before-switch",
    "max_executor_switches": "--max-executor-switches",
    "heartbeat_n_rounds": "--heartbeat-n",
    "checkpoint_every": "--checkpoint-every",
    "split_exit_threshold": "--split-exit-threshold",
    "retry_remaining_threshold": "--retry-remaining-threshold",
    "split_max_depth": "--split-max-depth",
    "split_max_total": "--split-max-total",
    "split_protocol_retries": "--split-protocol-retries",
    "end_gate": "--end-gate",
}
_BOOL_FLAGS = {
    "enable_split": "--enable-split",   # True → --enable-split；False → --no-split
}


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="config_cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    for cmd in ("dump-env", "run-flags"):
        sp = sub.add_parser(cmd)
        sp.add_argument("--cwd", default=None)
        sp.add_argument("--config", default=None)
    return p


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    cwd = Path(args.cwd) if args.cwd else None
    combined = resolve_combined(explicit_config=args.config, cwd=cwd)
    ro = combined["runtime_overrides"]
    menv = combined["model_env"]

    if args.cmd == "dump-env":
        print(json.dumps(menv, ensure_ascii=False))
        return 0

    # run-flags：只透传"runner cli 支持 + 有值"的键
    flags: list[str] = []
    for key, flag in _RUNTIME_TO_FLAG.items():
        if key in ro:
            flags.append(f"{flag} {ro[key]}")
    if "enable_split" in ro:
        flags.append(_BOOL_FLAGS["enable_split"] if ro["enable_split"] else "--no-split")
    print(" ".join(flags))
    return 0


if __name__ == "__main__":
    sys.exit(main())