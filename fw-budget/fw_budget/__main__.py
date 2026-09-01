"""python3.11 -m fw_budget 入口（等价 python3.11 -m fw_budget.cli）。"""
from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
