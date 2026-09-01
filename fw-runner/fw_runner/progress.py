"""executor 进度落档（可靠性补丁：轮数将尽/失败不丢半成品，换人从进度继续）。

核心机制（对应两条真实运行教训的修复）：
- 功能A：executor 每轮把「已完成 X / 剩 Y」写进模块根目录的 交付说明.md 的
  `## 进度快照` 小节（persona 铁律）；runner 在轮数将尽（剩余 ≤1 轮）或本轮
  失败/超时/中断时兜底 `ensure_progress`：executor 未落档 → 写入占位
  `executor 未落档进度，视为中断`，绝不静默半途而废。
- 功能B：换 executor / 回人时 `progress_snapshot` 给出交接进度（交付说明.md
  缺快照时从 REVIEW.md 已做/待办 兜底），`progress_briefing` 生成「这是前任
  做到的位置，从『剩余』继续，不要重做已完成部分」的提示词片段（随交接
  bundle + REVIEW 进度指针一起交给新 executor / 真人）。

可解析格式（交付说明.md 内独立小节，行格式固定）：
    ## 进度快照
    - 已完成: <内容>
    - 剩余: <内容>
    - 记录/轮次: <executor> 第 <n> 轮 ｜ 时间: <ISO> ｜ 来源: <executor|runner|REVIEW>
小节整块替换/追加，绝不触碰 改动内容/测试结果 等其它小节。
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .io_utils import atomic_write_text
from .model import ModuleSpec

PROGRESS_SECTION = "进度快照"
STALE_MARKER = "executor 未落档进度，视为中断"
DONE_KEY = "已完成"
REMAIN_KEY = "剩余"


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


# ---------- 通用小节读写（交付说明.md / REVIEW.md 共用排版规则） ----------

def _split_sections(text: str) -> Dict[str, List[str]]:
    """按 `## 标题` 切分；返回 {title: [lines]}。"""
    sections: Dict[str, List[str]] = {}
    current: Optional[str] = None
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return sections


def _rebuild(text: str, sections: Dict[str, List[str]],
             extra: Optional[Dict[str, List[str]]] = None) -> str:
    """按标题重建文本：每个标题下只用（extra 若覆盖则用它、否则用原内容），
    标题区旧内容被吞掉不再重复输出，非标题区普通行原样保留。"""
    out: List[str] = []
    lines = text.splitlines()
    i = 0
    n = len(lines)
    while i < n:
        ln = lines[i]
        if ln.startswith("## "):
            title = ln[3:].strip()
            out.append(ln)
            i += 1
            while i < n and not lines[i].startswith("## "):
                i += 1
            src = (extra or {}).get(title, sections.get(title, []))
            for cl in src:
                if cl.strip():
                    out.append(cl)
        else:
            out.append(ln)
            i += 1
    return "\n".join(out) + "\n"


def _has_section(sections: Dict[str, List[str]], name: str) -> bool:
    if name in sections:
        return True
    return any(t.startswith(name + "（") or t.startswith(name + "(") for t in sections)


def _section_lines(sections: Dict[str, List[str]], name: str) -> List[str]:
    if name in sections:
        return sections[name]
    for title, lines in sections.items():
        if title.startswith(name + "（") or title.startswith(name + "("):
            return lines
    return []


def upsert_section_file(path: str | Path, name: str, lines: Sequence[str]) -> None:
    """把文本文件（如 交付说明.md）的 name 小节整体替换为 lines；小节不存在则追加
    到文件末尾。保留其它小节。原子写。供 交付说明.md 进度快照 与 REVIEW 进度指针 共用。"""
    p = Path(path)
    text = p.read_text(encoding="utf-8") if p.is_file() else ""
    sections = _split_sections(text)

    target: Optional[str] = None
    if name in sections:
        target = name
    else:
        for t in sections:
            if t.startswith(name + "（") or t.startswith(name + "("):
                target = t
                break
    if target is not None:
        new_text = _rebuild(text, sections, {target: list(lines)})
    else:
        base = text.rstrip() + "\n" if text.strip() else "# 交付说明\n\n"
        new_text = base + f"## {name}\n" + "\n".join(str(l) for l in lines if str(l).strip()) + "\n"
    atomic_write_text(p, new_text)


# ---------- 功能A：交付说明.md 进度快照 ----------

def read_progress(delivery_path: str | Path) -> Dict[str, str]:
    """解析 交付说明.md 的 进度快照 小节 → {'已完成':…, '剩余':…, 'source':'delivery'}。

    无快照小节或无可解析的行 → 返回 {}。
    """
    p = Path(delivery_path)
    if not p.is_file():
        return {}
    sections = _split_sections(p.read_text(encoding="utf-8"))
    lines = _section_lines(sections, PROGRESS_SECTION)
    if not lines:
        return {}
    kv: Dict[str, str] = {}
    for ln in lines:
        for key in (DONE_KEY, REMAIN_KEY):
            prefix = f"- {key}:"
            stripped = ln.strip()
            if stripped.startswith(prefix):
                kv[key] = stripped[len(prefix):].strip()
    if not kv:
        return {}
    return {
        "已完成": kv.get(DONE_KEY, ""),
        "剩余": kv.get(REMAIN_KEY, ""),
        "source": "delivery",
    }


def write_progress(delivery_path: str | Path, *, done: str, remaining: str,
                   executor_id: str = "", round_no: int = 0,
                   source: str = "executor") -> None:
    """写/更新 交付说明.md 的 进度快照 小节（整块替换；不影响其它小节）。"""
    lines = [
        f"- {DONE_KEY}: {done}",
        f"- {REMAIN_KEY}: {remaining}",
        f"- 记录/轮次: {executor_id or '?'} 第 {round_no} 轮 ｜ 时间: {_now()} ｜ 来源: {source}",
    ]
    upsert_section_file(delivery_path, PROGRESS_SECTION, lines)


def ensure_progress(delivery_path: str | Path, *, executor_id: str = "",
                    round_no: int = 0) -> bool:
    """runner 兜底：交付说明.md 已有进度快照 → 不覆盖（返回 False）；
    没有 → 写入占位「executor 未落档进度，视为中断」（返回 True）。"""
    if read_progress(delivery_path):
        return False
    write_progress(delivery_path, done=STALE_MARKER, remaining=STALE_MARKER,
                   executor_id=executor_id, round_no=round_no,
                   source="runner(兜底:未落档)")
    return True


# ---------- 功能B：交接进度（交付说明.md 优先，REVIEW 已做/待办 兜底） ----------

def review_ledger_snapshot(module: ModuleSpec) -> Dict[str, str]:
    """REVIEW.md 已做/待办 兜底：已完成 = 已做节（去占位），剩余 = 待办节（去占位）。"""
    try:
        from .review import read_review  # 延迟导入避免与 review 循环依赖
        doc = read_review(module.review_path)
    except (OSError, FileNotFoundError):
        return {"已完成": STALE_MARKER, "剩余": STALE_MARKER, "source": "unknown"}
    done_items = [ln.strip()[2:].strip() for ln in doc.list_done() if "（占位）" not in ln]
    todo_items = [ln.strip()[6:].strip() for ln in doc.list_todo() if "（占位）" not in ln]
    return {
        "已完成": "；".join(done_items) if done_items else STALE_MARKER,
        "剩余": "；".join(todo_items) if todo_items else STALE_MARKER,
        "source": "REVIEW.md(已做/待办兜底)",
    }


def progress_snapshot(module: ModuleSpec) -> Dict[str, str]:
    """模块当前交接进度：优先 交付说明.md 进度快照；缺失时从 REVIEW.md 已做/待办 兜底。"""
    snap = read_progress(module.delivery_path)
    if snap:
        return snap
    return review_ledger_snapshot(module)


def progress_briefing(module: ModuleSpec, old_executor_id: str = "",
                      snap: Optional[Dict[str, str]] = None) -> str:
    """构造给新 executor 的提示词片段：「这是前任做到的位置，从『剩余』继续，
    不要重做已完成的部分」+ 前任进度指针（已完成/剩余/来源）。"""
    snap = snap or progress_snapshot(module)
    return (
        "【新任 executor 须知：前任进度（先读 交付说明.md 全文，从「剩余」继续，"
        "不要重做已完成的部分）】\n"
        f"- 前任 executor: {old_executor_id or '?'}\n"
        f"- 已完成: {snap.get('已完成', STALE_MARKER)}\n"
        f"- 剩余: {snap.get('剩余', STALE_MARKER)}\n"
        f"- 进度来源: {snap.get('source', '?')}\n"
    )