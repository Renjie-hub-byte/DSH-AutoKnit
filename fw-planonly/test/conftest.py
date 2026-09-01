"""pytest 公共夹具：把 src/ 加进 sys.path，让 ``import autoknit`` 与子进程 ``python -m autoknit`` 均可用。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))


@pytest.fixture()
def cli_env() -> dict[str, str]:
    """CLI 子进程所需环境：保证能找到 autoknit 包。"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    return env
