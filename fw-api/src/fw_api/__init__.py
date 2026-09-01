"""fw_api —— AutoKnit 对外接口层（收敛自 dsh_cockpit 系列验证实现）。

统一出口：任何前端（DSH client 插件 / 自建 cockpit / MCP）通过 ``fw_api.dsh.*``
消费 AutoKnit 任务状态与交互。普适、可随 AutoKnit 开源打包，不依赖 DSH 前端。

接口清单（契约 path → 入口）:
    dsh.task.list            fw_api.dsh.task.list(task_dir)
    dsh.task.detail          fw_api.dsh.task.detail(task_dir, run_id)
    dsh.task.update          fw_api.dsh.task.update.push(event)（广播，R→F）
    dsh.task.reply           fw_api.dsh.task.reply.submit(...)（needs_human 回复）
    dsh.session.detail       fw_api.dsh.session.detail(data_dir, session_id)
    dsh.usage.summary        fw_api.dsh.usage.summary(source)
    dsh.usage.update         fw_api.dsh.usage.update(...)

数据桥（只读）:
    fw_api.fwr_dir.read(task_dir)     任务目录解析（原 m01）
    fw_api.fwr_status.compute(raw)    阶段/模块状态计算（原 m02）

设计原则（与历史实现一致）:
  - 只读数据桥不写任务状态文件；确定性空降级、永不抛异常。
  - 所有接口返回确定性结构化 dict（字段执行期涌现后回填）。
"""

from __future__ import annotations

from types import SimpleNamespace

from . import fwr_dir, fwr_status  # 兼容命名空间（数据桥）
from .task_list_service import dsh as _dsh_task_list
from .task_detail import dsh as _dsh_task_detail
from . import task_update as _task_update_mod
from .session_detail import get_session_detail
from .usage_summary import dsh_usage_summary, summarize_session_dir
from .usage_update import build_payload as _usage_update_build
from . import reply as _reply_mod

__version__ = "0.1.0"


# ---- dsh.task.reply（needs_human 回复通道）----
class _reply_ns:
    submit = staticmethod(_reply_mod.submit_reply)
    submit_with_push = staticmethod(_reply_mod.submit_reply_with_push)


class _task:
    """dsh.task 命名空间：list / detail / update / reply。"""

    list = staticmethod(_dsh_task_list.task.list)
    detail = staticmethod(_dsh_task_detail.task.detail)
    update = _task_update_mod.dsh.task.update
    reply = _reply_ns


class _session:
    detail = staticmethod(get_session_detail)


class _usage:
    summary = staticmethod(dsh_usage_summary)
    update = staticmethod(_usage_update_build)


class _dsh_ns:
    task = _task
    session = _session
    usage = _usage


dsh = _dsh_ns()

# 顶层便利别名（保持对历史函数名的兼容）
list_tasks = _dsh_task_list.task.list
detail = _dsh_task_detail.task.detail
get_session_detail = get_session_detail
submit_reply = _reply_mod.submit_reply

__all__ = [
    "dsh",
    "fwr_dir",
    "fwr_status",
    "list_tasks",
    "detail",
    "get_session_detail",
    "submit_reply",
    "dsh_usage_summary",
    "summarize_session_dir",
]
