"""根 conftest：保证 fw_protocol 包可导入（不依赖以 -m 方式运行 pytest）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
