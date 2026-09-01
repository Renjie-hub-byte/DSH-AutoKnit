"""fwr_status 兼容命名空间 —— 收敛自 dsh_cockpit m02（阶段/模块状态计算）。

转调 fw_api.status（原 fwr_status.status）。契约 path: fwr.status.compute。
用法不变：
    import fwr_status
    status_result = fwr_status.compute(raw)
"""
from ..status import compute, compute_run, fwr, empty_result  # noqa: F401

__all__ = ["compute", "compute_run", "fwr", "empty_result"]
__version__ = "0.1.0"
