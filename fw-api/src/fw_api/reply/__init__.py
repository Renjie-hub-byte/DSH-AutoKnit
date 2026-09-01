"""dsh.task.reply —— M3 请示人工回复服务。

将人工对 needs_human 任务的回复指令（continue/retry/revise/自定义+说明）原子写入
任务目录内的回复通道文件 ``needs_human/reply.md``，供 fw-runner resume 侧读取生效。

对外主要入口：
    - :func:`dsh_reply.service.submit_reply`  —— 提交一条回复（dsh.task.reply, post）
    - :func:`dsh_reply.channel.write_reply`    —— 底层通道写入（不做 command 合法性校验）
    - :class:`dsh_reply.errors.ReplyError`     —— 确定性错误（reason∈[非needs_human,通道不可用,写失败]）
"""

from .bridge import (
    ReplyObservation,
    can_reply,
    needs_human_appeared,
    observe,
    reply_changed,
)
from .channel import CHANNEL_DIR_NAME, CHANNEL_FILE_NAME, WrittenReply, write_reply
from .effect import (
    ChannelNotReady,
    confirm_effect,
    is_reply_format_valid,
    parse_reply,
    read_channel,
)
from .errors import ReplyError, ReplyReason
from .push import (
    CHANNEL_ERROR,
    CHANNEL_RESP,
    InProcessPushSink,
    PushEvent,
    PushSink,
    build_error_item,
    build_resp_item,
)
from .service import (
    ALLOWED_COMMANDS,
    TaskDirResolver,
    submit_reply,
    submit_reply_with_push,
)

__all__ = [
    "CHANNEL_DIR_NAME",
    "CHANNEL_FILE_NAME",
    "WrittenReply",
    "write_reply",
    "ReplyError",
    "ReplyReason",
    "ALLOWED_COMMANDS",
    "TaskDirResolver",
    "submit_reply",
    "submit_reply_with_push",
    "PushEvent",
    "PushSink",
    "InProcessPushSink",
    "build_resp_item",
    "build_error_item",
    "CHANNEL_RESP",
    "CHANNEL_ERROR",
    "ChannelNotReady",
    "read_channel",
    "parse_reply",
    "confirm_effect",
    "is_reply_format_valid",
    "can_reply",
    "needs_human_appeared",
    "observe",
    "reply_changed",
    "ReplyObservation",
]

__version__ = "0.2.0"
