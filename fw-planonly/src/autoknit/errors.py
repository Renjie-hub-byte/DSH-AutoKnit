"""autoknit 领域异常与退出码约定。

退出码是模块对外的可观测契约（验收要求"规划完即停、退出码 0"），集中在此定义，
避免散落在各处造成不一致。
"""

from __future__ import annotations


class AutoknitError(Exception):
    """plan-only 流程的基类异常。"""

    exit_code = 1


class NoPrdFoundError(AutoknitError):
    """任务目录里找不到可用的 PRD 文件（无法规划）。"""

    exit_code = 2


class PlanNotReadyError(AutoknitError):
    """尚未产出 task.yaml / checkpoint，无法生成摘要（确定性空降级）。"""

    exit_code = 3


class InvalidTaskDirError(AutoknitError):
    """task.yaml 存在但内容不合法（无法解析/缺少必要字段）。"""

    exit_code = 4
