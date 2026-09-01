"""fwapi.dsh.reply —— 人工决策回复写入（dsh.task.reply → POST /api/runs/{id}/reply）。

封装 fw_api.reply 语义：把面板提交的 continue/retry/revise/custom 决策确定性写入：
1. needs_human/reply.md（跨模块共享存储，m02 面板/runner 侧消费）；
2. 总日志/human_answer.json（框架 runner 的 H2 口袋，apply_human_answers 在 --resume
   时读取收敛 needs_human 模块 —— 补齐「面板提交 → 框架恢复」的闭环）。

> 扩展点：服务端已装 fw-api（`import fw_api`）且其 `reply` 签名与本函数同构时，可在
> `_write_reply` 处复用；本环境未装 fw-api，故按任务书「否则同构实现」用标准库完成
> 相同落盘语义，绝不依赖 LLM / 不写任务状态文件。

边界：
- 只写 needs_human/reply.md + 总日志/human_answer.json，绝不改既有数据文件、绝不调 LLM；
- command 白名单校验（continue/retry/revise/custom/自定义），自定义必填 instruction；
- 仅允许对「当前确需人工决策」的 run 回复（经 总日志/快照.json needs_human 判定）；
- 成功/失败均确定性 JSON {success, detail}，绝不抛异常。
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional

from fwapi.dsh import task as task_source

# command 白名单（对齐契约 dsh.task.reply；同时收面板英文 custom 与框架「自定义」）。
COMMANDS: tuple = ("continue", "retry", "revise", "custom", "自定义")

# 决策回复落盘相对路径（文档化约定，跨模块共享存储，写仅限此文件）。
REPLY_FILE = os.path.join("needs_human", "reply.md")

# 框架 runner H2 口袋（apply_human_answers --resume 读取；与 human.py 同路径约定）。
HUMAN_ANSWER_REL = os.path.join("总日志", "human_answer.json")

# 面板命令 → 框架 human_answer.json code 映射（框架语义见 fw_runner/human.py）：
#   A=放弃, B=改方案重跑, C=暂停, D=自定义重跑；continue/retry/revise 均落到「重跑」，
#   差异经 text 记录；custom 用 D。
COMMAND_TO_CODE: Dict[str, str] = {
    "continue": "B",
    "retry": "B",
    "revise": "B",
    "custom": "D",
    "自定义": "D",
}

# 确定性错误 detail（供单测/错误信封断言稳定）。
ERR_EMPTY_RUN = "run_id 不能为空"
ERR_BAD_COMMAND = "command 不在白名单（continue/retry/revise/custom）"
ERR_CUSTOM_NO_INSTRUCTION = "自定义命令必须提供 instruction"
ERR_RUN_MISS = "task_dir 无效或 run 未命中"
ERR_NOT_PENDING = "该 run 当前不需要人工决策"
ERR_MISSING_MODULE = "需要提供 module_id 以定位待决策模块"
ERR_AMBIGUOUS_MODULE = "存在多个待决策模块，请提供 module_id"
ERR_WRITE_FAIL = "写入 reply.md 失败"
ERR_WRITE_ANSWER_FAIL = "写入 human_answer.json 失败"


def _validate(task_dir: str, run_id: str, command: Any, instruction: Any) -> Optional[str]:
    """参数白名单校验；合法返回 None，否则返回确定性错误 detail。"""
    if not run_id:
        return ERR_EMPTY_RUN
    if command not in COMMANDS:
        return ERR_BAD_COMMAND
    if command in ("custom", "自定义") and (not isinstance(instruction, str) or not instruction.strip()):
        return ERR_CUSTOM_NO_INSTRUCTION
    return None


def _ensure_run_pending(task_dir: str, run_id: str) -> Optional[str]:
    """校验该 run 当前确需人工决策（通道可用性）。

    经 get_run_tree 读取 快照.json：目录无效/未命中 → run 未命中；
    快照命中但 needs_human 为空 → 当前不需要人工决策。合法返回 None。
    """
    tree = task_source.get_run_tree(task_dir, run_id)
    if tree is None:
        return ERR_RUN_MISS
    if not tree["needs_human"]:
        return ERR_NOT_PENDING
    return None


def _render_reply(run_id: str, command: str, instruction: Any, at: str) -> str:
    """渲染 needs_human/reply.md 内容：命令/说明/时间齐全，确定性强。"""
    inst = instruction.strip() if isinstance(instruction, str) and instruction.strip() else "（无）"
    return (
        "# AutoKnit 人工决策回复\n\n"
        f"- run_id: {run_id}\n"
        f"- command: {command}\n"
        f"- instruction: {inst}\n"
        f"- at: {at}\n"
    )


def _now() -> str:
    """本地时间戳（ISO8601 风格，便于面板侧排序展示）。"""
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _write_reply(
    task_dir: str, run_id: str, command: str, instruction: Any, at: str
) -> Optional[str]:
    """写 needs_human/reply.md；成功返回 None，IO 失败返回确定性错误 detail。"""
    path = os.path.join(task_dir, REPLY_FILE)
    body = _render_reply(run_id, command, instruction, at)
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
    except OSError:
        return ERR_WRITE_FAIL
    return None


def _resolve_module(task_dir: str, run_id: str, params: Dict[str, Any]) -> Optional[str]:
    """解析待决策模块 id：params.module_id / moduleId 优先；缺失时 needs_human
    恰有一个则用该模块，多个则报歧义。返回模块 id 或 None（错误经 params 无法回传，
    由调用方按 None + 判定逻辑兜底——此处返回 None 且不产生副作用）。
    """
    mid = params.get("module_id")
    if mid is None:
        mid = params.get("moduleId")
    if mid is not None and str(mid).strip():
        return str(mid).strip()
    tree = task_source.get_run_tree(task_dir, run_id)
    pending = tree.get("needs_human") or [] if isinstance(tree, dict) else []
    if len(pending) == 1:
        return str(pending[0])
    return None


def _write_human_answer(
    task_dir: str, module_id: str, command: str, instruction: Any, at: str
) -> Optional[str]:
    """写 总日志/human_answer.json（框架 H2 口袋）：answers.{module_id} 幂等合并。

    格式对齐 fw_runner/human.py：{answers: {mid: {module, code, text, root, reason,
    answered_at}}}。code 由命令映射（COMMAND_TO_CODE）。原子写；IO 失败返回确定性
    错误 detail。
    """
    path = os.path.join(task_dir, HUMAN_ANSWER_REL)
    doc: Dict[str, Any] = {}
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                doc = loaded
        except (OSError, ValueError):
            doc = {}
    answers = doc.get("answers")
    if not isinstance(answers, dict):
        answers = {}
        doc["answers"] = answers
    text = instruction.strip() if isinstance(instruction, str) and instruction.strip() else ""
    answers[module_id] = {
        "module": module_id,
        "code": COMMAND_TO_CODE.get(command, "B"),
        "text": text,
        "root": "",
        "reason": "",
        "answered_at": at,
    }
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
        os.replace(tmp, path)
    except OSError:
        return ERR_WRITE_ANSWER_FAIL
    return None


def reply(task_dir: str, run_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """dsh.task.reply：执行决策写入，返回契约 {success, detail}。

    params 含 command/instruction（指令来源：请求 body/query 已合并进 params）。
    任何失败均确定性返回 {success: False, detail: <原因>}，绝不抛异常。
    """
    command = params.get("command")
    instruction = params.get("instruction")

    err = _validate(task_dir, run_id, command, instruction)
    if err is not None:
        return {"success": False, "detail": err}

    # 按注册表解析 task_dir（注册表未命中确定性失败；缺失回落请求级 task_dir），
    # 校验与落盘都用同一解析结果，避免「pending 判定在 A、落盘却在 B」。
    tdir = task_source._resolve_run_task_dir(task_dir, run_id)
    if not tdir:
        return {"success": False, "detail": ERR_RUN_MISS}

    err = _ensure_run_pending(tdir, run_id)
    if err is not None:
        return {"success": False, "detail": err}

    module_id = _resolve_module(tdir, run_id, params)
    if not module_id:
        tree = task_source.get_run_tree(tdir, run_id)
        pending = tree.get("needs_human") or [] if isinstance(tree, dict) else []
        return {"success": False,
                "detail": ERR_AMBIGUOUS_MODULE if len(pending) > 1 else ERR_MISSING_MODULE}

    at = _now()
    err = _write_reply(tdir, run_id, command, instruction, at)
    if err is not None:
        return {"success": False, "detail": err}

    err = _write_human_answer(tdir, module_id, command, instruction, at)
    if err is not None:
        return {"success": False, "detail": err}

    return {"success": True,
            "detail": "reply 已写入 needs_human/reply.md 与 总日志/human_answer.json"}
