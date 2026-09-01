"""fwr_dir 兼容命名空间 —— 收敛自 dsh_cockpit m01（任务目录解析数据桥）。

转调 fw_api.dir_reader（原 fwr_dir.reader）。契约 path: fwr.dir.read。
用法不变：
    import fwr_dir
    data = fwr_dir.read(task_dir)        # = fwr_dir.fwr.dir.read(task_dir)
"""
from ..dir_reader import read_dir, read, fwr, empty_result  # noqa: F401

__all__ = ["read_dir", "read", "fwr", "empty_result"]
__version__ = "0.1.0"
