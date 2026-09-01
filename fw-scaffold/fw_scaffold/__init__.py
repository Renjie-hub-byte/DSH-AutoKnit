"""fw-scaffold —— 目录脚手架（dsh 任务编排层 v1.0 / 需求 2）。

读合法 task.yaml（经 fw-protocol validate_file 判定）一键生成 v2 目录树：
任务-<名>_<日期>/  + contracts/api.yaml + skeleton.md + 认知/ + shared/(只读共享)
+ 总日志/（dispatch.jsonl + integration.jsonl + 快照.json）+ modules/mXX-<名>/（
src + test + logs/ + tmp/ + REVIEW.md + contract.yaml + 任务书-mXX.yaml + 交付说明.md）。

公开 API：
    from fw_scaffold import generate, scaffold_task, ExpectedVersionMismatch, ScaffoldResult

CLI：python3.11 -m fw_scaffold.cli task.yaml [--output DIR] [--force] [--dry-run] [--json]
退出码：0=生成成功 1=任务书校验失败 2=版本防护冲突(需 --force/换目录) 3=IO/依赖 4=usage
"""
from .scaffold import ScaffoldResult, TaskInvalidError, generate, scaffold_task
from .io_utils import ExpectedVersionMismatch

__all__ = ["ScaffoldResult", "TaskInvalidError", "generate", "scaffold_task", "ExpectedVersionMismatch"]
__version__ = "1.0.0"
