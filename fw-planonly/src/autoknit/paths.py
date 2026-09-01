"""路径解析。

对齐共享数据契约：快照(checkpoint)落在 ``总日志/快照.json``，并支持
``FW_SNAPSHOT_PATH`` 环境变量覆盖；事件日志与 token 账本作为模块内部产物也放在
``总日志/`` 下。路径解析全部集中在此，保证全模块一致、可测试（可注入 base dir）。
"""

from __future__ import annotations

import os
from pathlib import Path

# 与共享数据契约一致的中文目录名/文件名，禁止自定义。
LOG_DIR_NAME = "总日志"
SNAPSHOT_NAME = "快照.json"
HUMAN_ANSWER_NAME = "human_answer.json"
EVENTS_NAME = "events.jsonl"
TOKENS_NAME = "tokens.json"
PLAN_CHECKPOINT_NAME = "plan_checkpoint.json"
TASK_YAML_NAME = "task.yaml"

# snapshot 路径可用环境变量覆盖（与数据契约 env_var 对齐）。
SNAPSHOT_ENV = "FW_SNAPSHOT_PATH"


class TaskPaths:
    """给定任务根目录，解析 plan-only 用到的全部路径。

    ``base`` 参数用于测试注入，默认取真实任务目录。路径均为绝对 Path。
    """

    def __init__(self, task_dir: str | os.PathLike[str], base: str | os.PathLike[str] | None = None) -> None:
        base_root = Path(base) if base else Path.cwd()
        self.task_dir = (base_root / task_dir).resolve() if not os.path.isabs(task_dir) else Path(task_dir).resolve()
        self.log_dir = self.task_dir / LOG_DIR_NAME

    @property
    def prd_candidates(self) -> list[str]:
        # 确定性搜索顺序：PRD.md / prd.md / 任务-*.md / 单一 .md。
        return ["PRD.md", "prd.md"]

    @property
    def task_yaml(self) -> Path:
        return self.task_dir / TASK_YAML_NAME

    @property
    def snapshot_path(self) -> Path:
        env = os.environ.get(SNAPSHOT_ENV)
        if env:
            return Path(env).expanduser().resolve()
        return self.log_dir / SNAPSHOT_NAME

    @property
    def human_answer_path(self) -> Path:
        return self.log_dir / HUMAN_ANSWER_NAME

    @property
    def events_path(self) -> Path:
        return self.log_dir / EVENTS_NAME

    @property
    def tokens_path(self) -> Path:
        return self.log_dir / TOKENS_NAME

    @property
    def plan_checkpoint_path(self) -> Path:
        return self.log_dir / PLAN_CHECKPOINT_NAME

    def ensure_log_dir(self) -> Path:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        return self.log_dir
