"""REVIEW.md 读写助手（模块验收闭环的机器可解析载体，fw-scaffold 模板的延伸）。

- parse_key_values : 解析 `键: 值` 行（容忍尾部 `# 注释`、键内含空格与连字符）
- read_review      : 完整解析（键值 + 各 `## 小节` 的行）
- set_values       : 更新指定键值行（保留注释与排版），整文件原子重写
- append_done / append_todo : 向 `## 已做` / `## 待办` 追加 `- 条目`（不重复）
- fingerprint      : 模块实质产出指纹（心跳守护用：已做节 + status + 产物文件变化）

单一写者纪律：机器可解析状态键（status/executor_round/auditor_round/root/confidence/
executor_id/block_count/...）统一由 runner 依据 driver outcome 写回 —— executor/auditor
只写内容小节（已做/待办/交接/交付说明），避免并发写冲突（配合 fs 原子写，无需外部锁）。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .io_utils import atomic_write_text  # noqa: E402
from .model import ModuleSpec  # noqa: E402

KEY_RE = re.compile(r"^([A-Za-z][\w .\-]*?):\s*(.*?)\s*(?:#.*)?$")


@dataclass
class ReviewDoc:
    """REVIEW.md 的结构化视图。"""

    path: Path
    kv: Dict[str, str] = field(default_factory=dict)
    sections: Dict[str, List[str]] = field(default_factory=dict)
    raw: str = ""

    def list_done(self) -> List[str]:
        sec = _section_lines(self.sections, "已做")
        return [ln for ln in sec if ln.strip().startswith("- ")]

    def list_todo(self) -> List[str]:
        sec = _section_lines(self.sections, "待办")
        return [ln for ln in sec if ln.strip().startswith("- [ ]")]


def parse_key_values(text: str) -> Dict[str, str]:
    kv: Dict[str, str] = {}
    for line in text.splitlines():
        m = KEY_RE.match(line.rstrip())
        if m:
            kv[m.group(1).strip()] = m.group(2).strip()
    return kv


def _split_sections(text: str) -> Tuple[Dict[str, List[str]], List[str]]:
    """按 `## 标题` 切分；返回 {title: [lines]} 与顶层（标题前）行。"""
    sections: Dict[str, List[str]] = {}
    top: List[str] = []
    current: Optional[str] = None
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            sections[current] = []
        elif current is None:
            top.append(line)
        else:
            sections[current].append(line)
    return sections, top


def _section_lines(sections: Dict[str, List[str]], name: str) -> List[str]:
    """按小节短名取行：匹配标题 == name 或 以 name+（ 开头（模板标题带后缀说明，如
    '## 已做（done，按事件 seq 或时间记录，可追溯）'）。"""
    if name in sections:
        return sections[name]
    for title, lines in sections.items():
        if title.startswith(name + "（") or title.startswith(name + "("):
            return lines
    return []


def _section_set(sections: Dict[str, List[str]], name: str,
                 lines: Sequence[str]) -> Dict[str, List[str]]:
    """把小节行写回 sections（按短名匹配标题；name 不存在时新建小节）。"""
    out = dict(sections)
    out[name] = list(lines)
    for title in list(out.keys()):
        if title.startswith(name + "（") or title.startswith(name + "("):
            out[title] = list(lines)
    return out


def read_review(path: str | Path) -> ReviewDoc:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"REVIEW.md 不存在: {p}")
    raw = p.read_text(encoding="utf-8")
    sections, _top = _split_sections(raw)
    return ReviewDoc(path=p, kv=parse_key_values(raw), sections=sections, raw=raw)


def _apply_kv_to_line(line: str, kv: Mapping[str, str]) -> str:
    m = KEY_RE.match(line.rstrip())
    if m and m.group(1).strip() in kv:
        key = m.group(1).strip()
        return f"{key}: {kv[key]}"
    return line


def _rewrite(path: Path, kv: Mapping[str, str],
             sections: Mapping[str, Sequence[str]]) -> None:
    """按键值更新 + 小节内容重建 REVIEW.md（保留结构与排版，原子重写）。"""
    raw = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = raw.splitlines()
    old_sections, top = _split_sections(raw)
    merged = dict(old_sections)
    for title, new_lines in (sections or {}).items():
        merged[title] = list(new_lines)

    out: List[str] = []
    new_keys_added = False
    idx = 0
    while idx < len(lines):
        ln = lines[idx]
        if ln.startswith("## "):
            title = ln[3:].strip()
            out.append(ln)
            idx += 1
            # 跳过原小节内容，用 merged 内容（内容行也套用 kv 替换，如 现象:/detail:）
            for cl in merged.get(title, []):
                if cl.strip():
                    out.append(_apply_kv_to_line(cl, kv))
            while idx < len(lines) and not lines[idx].startswith("## "):
                idx += 1
            continue
        out.append(_apply_kv_to_line(ln, kv))
        idx += 1
    # 新增键（原文件中不存在）：插在第一个 ## 标题前
    existing = parse_key_values(raw)
    new_kv = {k: v for k, v in kv.items() if k not in existing}
    if new_kv:
        first_heading = next((i for i, l in enumerate(out) if l.startswith("## ")), len(out))
        tail = out[first_heading:]
        insert = [f"{k}: {v}" for k, v in new_kv.items()]
        out = out[:first_heading] + insert + tail
    # 新增小节（原文件没有的标题）：追加到文件末尾
    existing_titles = set(old_sections.keys())
    for title, new_lines in merged.items():
        if title not in existing_titles:
            out.append("")
            out.append(f"## {title}")
            for cl in new_lines:
                if cl.strip():
                    out.append(_apply_kv_to_line(cl, kv))
    atomic_write_text(path, "\n".join(out) + "\n")


def upsert_section_file(path: str | Path, section: str,
                        lines: Sequence[str]) -> None:
    """整块替换 REVIEW.md 中的一个小节（幂等：不追加重复，顶层键值保留）。"""
    p = Path(path)
    doc = read_review(p)
    sections = _section_set(doc.sections, section, list(lines))
    _rewrite(p, doc.kv, sections)


def set_values(path: str | Path, **kv: str) -> None:
    p = Path(path)
    doc = read_review(p)
    doc.kv.update({k: str(v) for k, v in kv.items()})
    _rewrite(p, doc.kv, doc.sections)


def append_done(path: str | Path, line: str) -> bool:
    p = Path(path)
    doc = read_review(p)
    entry = f"- {line}"
    if any(x.strip() == entry for x in doc.list_done()):
        return False
    sections = _section_set(doc.sections, "已做",
                            _section_lines(doc.sections, "已做") + [entry])
    _rewrite(p, doc.kv, sections)
    return True


def append_todo(path: str | Path, line: str) -> bool:
    p = Path(path)
    doc = read_review(p)
    entry = f"- [ ] {line}"
    if any(x.strip() == entry for x in doc.list_todo()):
        return False
    sections = _section_set(doc.sections, "待办",
                            _section_lines(doc.sections, "待办") + [entry])
    _rewrite(p, doc.kv, sections)
    return True


def write_handover(path: str | Path, kv: Mapping[str, str], text: str) -> None:
    """写交接说明（现象/已试办法等）—— 换 executor 交接三件套的一部分。"""
    p = Path(path)
    doc = read_review(p)
    sections = dict(doc.sections)
    sections["交接说明"] = list(text.splitlines()) + [""]
    merged_kv = dict(doc.kv)
    merged_kv.update({k: str(v) for k, v in kv.items()})
    _rewrite(p, merged_kv, sections)


def handover_bundle(module: ModuleSpec) -> str:
    """交接三件套汇总文本：REVIEW.md（含判定与交接说明）+ contract.yaml + 交付说明.md。"""
    parts: List[str] = []
    for title, path in (
        ("REVIEW.md", module.review_path),
        ("contract.yaml", module.contract_path),
        ("交付说明.md", module.delivery_path),
    ):
        parts.append(f"===== {title} =====")
        if path.is_file():
            parts.append(path.read_text(encoding="utf-8").rstrip())
        else:
            parts.append(f"（缺失: {path}）")
        parts.append("")
    return "\n".join(parts)


def fingerprint(module: ModuleSpec) -> str:
    """模块实质产出指纹（心跳守护：连续 N 轮指纹不变 → 判静默卡死）。

    纳入：REVIEW.md 已做节 + status 键 + src/ test/ 下文件（名+大小+mtime）+
    交付说明.md；排除 logs/ tmp/（auditor 豁免区）。
    """
    h = hashlib.sha256()
    try:
        doc = read_review(module.review_path)
    except FileNotFoundError:
        doc = ReviewDoc(path=module.review_path)
    h.update("|done|".encode())
    for ln in doc.list_done():
        h.update(ln.encode())
    h.update(("|status|" + doc.kv.get("status", "")).encode())
    for sub in ("src", "test"):
        base = module.dir / sub
        if base.is_dir():
            for p in sorted(base.rglob("*")):
                if p.is_file() and not p.name.startswith("."):
                    try:
                        st = p.stat()
                        h.update(f"{p.relative_to(module.dir)}:{st.st_size}:{int(st.st_mtime)}|".encode())
                    except OSError:
                        pass
    if module.delivery_path.is_file():
        try:
            st = module.delivery_path.stat()
            h.update(f"delivery:{st.st_size}:{int(st.st_mtime)}|".encode())
        except OSError:
            pass
    return h.hexdigest()
