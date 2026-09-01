"""pytest 共享配置：把 src/ 加入 sys.path，暴露 fixtures 目录路径。"""

import os
import sys

MODULE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(MODULE_DIR, "src")
FIXTURES_DIR = os.path.join(MODULE_DIR, "test", "fixtures")

if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)


def _fix(name):
    return os.path.join(FIXTURES_DIR, name)


def snapshot_path():
    return _fix("snapshot.json")


def snapshot_contract_path():
    return _fix("snapshot_contract.json")


def dispatch_path():
    return _fix("dispatch.jsonl")


def human_pending_path():
    return _fix("human_pending.json")
