"""fw-protocol —— 任务书协议与校验器（dsh 任务编排层 v1.0 / 需求 1）。

公开 API：
    from fw_protocol import validate_document, validate_file, ValidationResult, Issue
    from fw_protocol.schema import load_schema, apply_defaults

CLI：python3.11 -m fw_protocol.cli task.yaml [--json] [--no-cycle] [--no-interface] [--no-conflict]
退出码：0=pass 1=error 2=conflict(需人工定优先级) 3=io/schema 4=usage
"""
from .model import Issue, ValidationResult
from .validate import validate_document, validate_file

__all__ = ["Issue", "ValidationResult", "validate_document", "validate_file"]
__version__ = "1.0.0"
