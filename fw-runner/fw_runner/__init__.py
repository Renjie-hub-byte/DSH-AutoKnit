"""fw-runner —— 执行编排主循环（dsh 之上任务编排层 / 需求 4）。

公开 API：
    from fw_runner import run
    from fw_runner.runner import RunInterrupted, run
    from fw_runner.drivers import InlineAgentDriver, ScriptedAgentDriver, AgentContext
    from fw_runner.model import DriverOutcome, RunnerResult

CLI：
    python3.11 -m fw_runner.cli run TASK_ROOT [--max-parallel N] [--resume-from-checkpoint] ...
退出码：0=complete 1=input_error 2=needs_human 3=io_error 4=usage 130=interrupted
"""
from .model import DriverOutcome, RunnerResult  # noqa: F401
from .runner import RunInterrupted, run  # noqa: F401

__all__ = ["run", "RunInterrupted", "RunnerResult", "DriverOutcome"]
__version__ = "1.0.0"
