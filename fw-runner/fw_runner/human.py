"""v1.0 H：人机通道最小实现（H1–H2）。

设计-v1.0-简化版.md 第十节：terminal 交互，不依赖 GUI/企微。
  - H1：HUMAN 触发 → 打印「模块 mXX 需要人工决策」+ 四个预定义选项
  - H2：stdin 读取真人回复 → 写入 human_answer.json → resume 继续

交互开关（FW_HUMAN_INTERACTIVE）：
  - 1/true/yes/on  → 阻塞式 stdin 交互（真人终端；回复写入 human_answer.json）
  - 0/false/no/off → 强制非交互（headless/CI/测试：不读 stdin、不阻塞、不写答案文件）
  - 未设置         → 自动判断：非 pytest 环境且 stdin 是 TTY 时交互，否则非交互

resume 收敛（apply_human_answers，H2）：
  - [A]放弃该模块 → 该模块按完成跳过（不再执行，记录为人工放弃）
  - [B]改方案 / [D]自定义 → 该模块重置 pending 重新执行（改方案后给全新耐心）
  - [C]暂停任务   → 整个任务保持暂停（等待真人继续处理）
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from .context import TaskContext
from .events import EventLog
from .io_utils import atomic_write_text
from .model import RunState, now_iso
from .upgrade import finalize_human

HUMAN_ANSWER_REL = "总日志/human_answer.json"

# H1：预定义选项（设计文档第十节；H1 验收要求 [A]放弃 [B]改方案 [C]暂停 [D]自定义）
HUMAN_OPTIONS: Dict[str, str] = {
    "A": "放弃该模块",
    "B": "改方案",
    "C": "暂停任务",
    "D": "自定义",
}


def human_answer_path(ctx: TaskContext) -> Path:
    """human_answer.json 路径（与快照同目录 总日志/，resume 时同根读取）。"""
    return ctx.task_root / HUMAN_ANSWER_REL


def interactive_human_enabled(env: Optional[Mapping] = None,
                              stdin: Any = None) -> bool:
    """H1/H2 交互开关：显式 env 优先，其次自动判断（真机 TTY、非 pytest）。

    测试/CI/headless 一律非交互（不阻塞、不读 stdin），保证主循环在无真人时
    仍能走完（回人状态 + 退出码 2 由既有语义负责）。
    """
    env = os.environ if env is None else env
    flag = str(env.get("FW_HUMAN_INTERACTIVE") or "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    if flag in ("0", "false", "no", "off"):
        return False
    if "pytest" in sys.modules:      # 测试环境一律非交互，避免阻塞挂起
        return False
    try:
        return bool((sys.stdin if stdin is None else stdin).isatty())
    except Exception:
        return False


def prompt_text(mid: str, reason: str = "") -> str:
    """H1：拼装「模块 mXX 需要人工决策」+ 选项文本（不打印）。"""
    lines = [f"模块 {mid} 需要人工决策"]
    if reason:
        lines.append(f"原因: {reason}")
    lines.append("请选择处理方式：")
    for code, label in HUMAN_OPTIONS.items():
        lines.append(f"  [{code}] {label}")
    lines.append("（输入选项字母；[D] 可附自定义说明，如 D: 调整交付物范围）")
    return "\n".join(lines)


def prompt_human(mid: str, reason: str = "") -> str:
    """H1：打印提示并返回提示文本。"""
    text = prompt_text(mid, reason)
    print(text, flush=True)
    return text


def read_human_input(mid: str, reason: str = "",
                     input_fn: Callable[[str], str] = input) -> Tuple[str, str]:
    """H2：stdin 读取真人回复，返回 (code, text)。

    无效/空输入兜底为 D（自定义），原文保留在 text 供 resume 使用。
    """
    raw = (input_fn("请输入处理选项（A/B/C/D）: ") or "").strip()
    code = raw[:1].upper()
    text = raw[1:].lstrip(":： \t").strip()
    if code not in HUMAN_OPTIONS:
        code = "D"
        text = raw or "（未提供有效选项，按自定义处理）"
    return code, text


def write_human_answer(ctx: TaskContext, mid: str, code: str, text: str,
                       root: str = "", reason: str = "") -> Path:
    """H2：把真人回复写入 human_answer.json（按模块幂等合并，保留历史）。"""
    path = human_answer_path(ctx)
    doc: Dict[str, Any] = {}
    if path.is_file():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            doc = {}
    answers = doc.get("answers")
    if not isinstance(answers, dict):
        answers = {}
        doc["answers"] = answers
    answers[mid] = {
        "module": mid,
        "code": str(code).upper(),
        "text": text,
        "root": root,
        "reason": reason,
        "answered_at": now_iso(),
    }
    atomic_write_text(path, json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
    return path


def read_human_answer(ctx: TaskContext, mid: Optional[str] = None) -> Any:
    """H2：读 human_answer.json。mid 为空返回 {mid: answer} 全量；否则返回该模块回复（无则空 dict）。"""
    path = human_answer_path(ctx)
    if not path.is_file():
        return {} if mid is None else {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {} if mid is None else {}
    answers = doc.get("answers") if isinstance(doc, dict) else None
    if not isinstance(answers, dict):
        return {} if mid is None else {}
    if mid is not None:
        ans = answers.get(mid)
        return ans if isinstance(ans, dict) else {}
    return answers


def human_escalate(ctx: TaskContext, state: RunState, mid: str,
                   events: EventLog, root: str = "", reason: str = "",
                   interactive: Optional[bool] = None) -> str:
    """H1+H2 总入口：先落既有回人语义（finalize_human），再走人机通道。

    返回 HUMAN（回人语义不变，调用方据此结束该模块生命周期）。
    交互模式（TTY）阻塞读真人回复；非交互（headless/CI）写答案模板文件
    （总日志/human_answer.json），真人编辑后 --resume 读回续跑。
    """
    out = finalize_human(ctx, state, mid, events, root=root, reason=reason)
    if interactive is None:
        interactive = interactive_human_enabled()
    reason_txt = reason or state.ensure(mid).reason
    prompt_human(mid, reason_txt)
    if interactive:
        code, text = read_human_input(mid, reason_txt)
        write_human_answer(ctx, mid, code, text, root=root, reason=reason)
    else:
        # 非交互模式：写答案模板，真人编辑后 --resume 读回
        _write_human_template(ctx, mid, root=root, reason=reason_txt)
    return out


def _write_human_template(ctx: TaskContext, mid: str, root: str = "",
                          reason: str = "") -> None:
    """非交互模式下写答案模板文件，供真人手动编辑。

    写到 总日志/human_answer.json 的 answers.{mid} 条目，code 初始为 "?"，
    真人改为 A/B/C/D 后 --resume 读回生效。
    """
    path = human_answer_path(ctx)
    doc: Dict[str, Any] = {}
    if path.is_file():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            doc = {}
    answers = doc.get("answers")
    if not isinstance(answers, dict):
        answers = {}
        doc["answers"] = answers
    if mid not in answers:
        answers[mid] = {
            "module": mid,
            "code": "?",
            "text": "（请改为 A/B/C/D，然后 --resume）",
            "root": root,
            "reason": reason,
            "answered_at": "",
        }
    atomic_write_text(path, json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
    print(f"[human] 答案模板已写入 {path}（code='?'），请编辑后 --resume", flush=True)


def apply_human_answers(ctx: TaskContext, state: RunState,
                        events: EventLog) -> str:
    """H2 resume：按 human_answer.json 收敛 needs_human 模块。

    返回 "paused"（存在 [C]暂停任务）或 "ok"。
      - A（放弃该模块）→ 按完成跳过（不再执行）
      - B（改方案）/D（自定义）→ 重置 pending 重新执行（改方案后给全新耐心）
      - C（暂停任务）→ 任务保持暂停
    """
    answers = read_human_answer(ctx)
    if not answers:
        return "ok"
    paused = False
    for mid, ans in answers.items():
        if mid not in state.needs_human:
            continue
        code = str(ans.get("code") or "").upper()
        if code == "?":
            continue   # 模板未编辑，跳过
        if code == "A":
            astate = state.ensure(mid)
            astate.ended_at = now_iso()
            astate.reason = (ans.get("reason") or astate.reason or "") + "（人工放弃）"
            state.modules[mid] = "done"
            state.needs_human = [m for m in state.needs_human if m != mid]
            if mid not in state.completed_order:
                state.completed_order.append(mid)
            events.emit("module.human_abandoned", module=mid, detail={
                "code": "A", "text": ans.get("text") or "",
                "reason": astate.reason, "root": ans.get("root") or "",
            })
        elif code == "C":
            paused = True            # 任务暂停，等待真人继续处理
        else:
            # B 改方案 / D 自定义：改方案后重新执行该模块
            astate = state.ensure(mid)
            astate.executor_round = 0
            astate.auditor_round = 0
            astate.executor_switches = 0
            astate.block_count = 0
            astate.block_total = 0
            astate.model_tier = 0
            astate.last_verdict = ""
            state.modules[mid] = "pending"
            state.needs_human = [m for m in state.needs_human if m != mid]
            events.emit("module.human_rerun", module=mid, detail={
                "code": code, "text": ans.get("text") or "",
                "reason": ans.get("reason") or "", "root": ans.get("root") or "",
            })
    return "paused" if paused else "ok"
