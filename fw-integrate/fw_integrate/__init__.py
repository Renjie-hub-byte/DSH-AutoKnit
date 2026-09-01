"""fw-integrate —— 集成验收模块（dsh 之上任务编排层 / 需求 6）。

读取契约区与各模块产物做**运行时契约校验**（接口匹配 / 数据格式）、**跨模块数据依赖检查**
（B 需要的输入是否 A 的 output 声明过）、**预测基线 will_have/will_not_have 对照**
（输出匹配/缺失清单）；按 end_gate（auto=异常才找人 / always=人工确认）决定是否上抛回人；
全部通过时产出完成报告并触发归档（复用 fw-budget 归档机制，见 archive 模块）。

公开 API：
    from fw_integrate import run_checks, complete_and_archive, FwIntegrateHook
    from fw_integrate.checks import check_interfaces, check_data_format, check_data_dependency
    from fw_integrate.baseline import check_baseline
    from fw_integrate.report import IntegrationCheckReport, build_completion_report

CLI：
    python3.11 -m fw_integrate.cli check TASK_ROOT [--json]
    python3.11 -m fw_integrate.cli complete TASK_ROOT [--reason TEXT] [--json]
    python3.11 -m fw_integrate.cli run TASK_ROOT [--executor-cmd CMD] [--auditor-cmd CMD] [--json]
退出码：0=ok(pass/归档) 1=input_error 2=needs_human(集成失败/需人工确认) 3=io_error 4=usage
"""
from .checks import check_data_dependency, check_data_format, check_interfaces  # noqa: F401
from .baseline import check_baseline  # noqa: F401
from .report import IntegrationCheckReport, build_completion_report, run_checks  # noqa: F401
from .archive import CompletionArchiveResult, complete_and_archive, confirm_and_archive  # noqa: F401
from .hook import FwIntegrateHook  # noqa: F401

__all__ = [
    "run_checks", "complete_and_archive", "confirm_and_archive", "FwIntegrateHook",
    "check_interfaces", "check_data_format", "check_data_dependency", "check_baseline",
    "IntegrationCheckReport", "build_completion_report", "CompletionArchiveResult",
]
__version__ = "1.0.0"
