"""paths —— 数据存储路径解析（对齐数据契约 data_contract.stores）。

路径优先级：
  * 快照   snapshot_store ：FW_SNAPSHOT_PATH 或 {task_root}/总日志/快照.json
  * 事件流 事件流          ：FW_DISPATCH_PATH 或 {task_root}/总日志/dispatch.jsonl
  * 待决策  human_pending  ：FW_HUMAN_PENDING 或 {task_root}/总日志/human_pending.json（可缺失）
  * 真人答案 human_answer  ：FW_HUMAN_ANSWER  或 {task_root}/总日志/human_answer.json（后续轮次写）

task_root 默认取环境变量 TASK_ROOT；显式传入时优先。
"""

import os

SNAPSHOT_REL = os.path.join("总日志", "快照.json")
DISPATCH_REL = os.path.join("总日志", "dispatch.jsonl")
HUMAN_PENDING_REL = os.path.join("总日志", "human_pending.json")
HUMAN_ANSWER_REL = os.path.join("总日志", "human_answer.json")

_SNAPSHOT_ENV = "FW_SNAPSHOT_PATH"
_DISPATCH_ENV = "FW_DISPATCH_PATH"
_HUMAN_PENDING_ENV = "FW_HUMAN_PENDING"
_HUMAN_ANSWER_ENV = "FW_HUMAN_ANSWER"


def resolve_task_root(explicit=None):
    """返回任务根目录：显式参数 > 环境变量 TASK_ROOT。"""
    if explicit:
        return explicit
    root = os.environ.get("TASK_ROOT")
    if root:
        return root
    # 兜底：按本模块相对位置向上找任务根（modules/<id> 的上级）。
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.dirname(here)


def _resolve(env_var, default_abs):
    env = os.environ.get(env_var)
    if env:
        return env
    return default_abs


def snapshot_path(task_root=None):
    return _resolve(_SNAPSHOT_ENV, os.path.join(resolve_task_root(task_root), SNAPSHOT_REL))


def dispatch_path(task_root=None):
    return _resolve(_DISPATCH_ENV, os.path.join(resolve_task_root(task_root), DISPATCH_REL))


def human_pending_path(task_root=None):
    return _resolve(_HUMAN_PENDING_ENV, os.path.join(resolve_task_root(task_root), HUMAN_PENDING_REL))


def human_answer_path(task_root=None):
    return _resolve(_HUMAN_ANSWER_ENV, os.path.join(resolve_task_root(task_root), HUMAN_ANSWER_REL))
