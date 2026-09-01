"""预测基线对照（需求6 检查项三）：task.prediction_baseline will_have / will_not_have。

目标语义（v0.4）：规划期写下的“最终交付会有什么 / 不会有什么”，执行后由 fw-integrate 程序对照
模块真实产物，输出**匹配/缺失清单**（验收2），不只人工比对。

自动对照策略（诚实标注为关键词启发式，见 docs 已知限制）：
- 关键词提取：
  - 文件样 token（含 `/` 或 `.ext`，如 src/data/orders.json / daily_orders.csv）→ 按相对路径
    存在性匹配（任务根 / modules/ 各模块目录 / shared/ contracts/ 下）。
  - 中文连续片段（≥2 字）与英文词（≥3 字母）→ 在交付物文本中搜索。
- 证据面（交付物，排除 auditor 豁免区 logs/ tmp/ 与 .auditor-ignore）：
  - 文件相对路径（modules/*/src、modules/*/test、shared/、contracts/、skeleton.md、认知/）
  - 文件内容（文本文件，UTF-8，每文件最多读 8KB）
  - 各模块 REVIEW.md（已做节与全文）与 交付说明.md
- 判定：
  - will_have 项：任一关键词命中 → matched（带证据路径）；全部落空 → missing。
  - will_not_have 项：任一关键词命中 → violation（带证据）；全部落空 → clean。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .context import IntegrateContext

_FILENAME_TOKEN_RE = re.compile(r"[\w./\-\\]+\.[A-Za-z0-9]{1,6}")
_ASCII_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,}")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
_IGNORE_DIRS = ("logs", "tmp")
_IGNORE_FILES = (".auditor-ignore", ".gitkeep", ".readonly", ".scaffold-manifest.json")
_MAX_READ = 8192


@dataclass
class BaselineItem:
    """一条预测基线的对照结果（机器可解析）。"""

    kind: str              # will_have | will_not_have
    item: str
    status: str            # matched | missing | clean | violation
    evidence: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "item": self.item, "status": self.status,
                "evidence": list(self.evidence)}


@dataclass
class BaselineResult:
    """预测基线对照完整结果：匹配/缺失/违反清单。"""

    ok: bool
    items: List[BaselineItem] = field(default_factory=list)

    @property
    def matched(self) -> List[str]:
        return [b.item for b in self.items if b.kind == "will_have" and b.status == "matched"]

    @property
    def missing(self) -> List[str]:
        return [b.item for b in self.items if b.kind == "will_have" and b.status == "missing"]

    @property
    def violations(self) -> List[str]:
        return [b.item for b in self.items if b.kind == "will_not_have" and b.status == "violation"]

    @property
    def clean(self) -> List[str]:
        return [b.item for b in self.items if b.kind == "will_not_have" and b.status == "clean"]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "counts": {"will_have_matched": len(self.matched),
                       "will_have_missing": len(self.missing),
                       "will_not_have_clean": len(self.clean),
                       "will_not_have_violation": len(self.violations)},
            "matched": [b.to_dict() for b in self.items if b.kind == "will_have" and b.status == "matched"],
            "missing": [b.to_dict() for b in self.items if b.kind == "will_have" and b.status == "missing"],
            "violations": [b.to_dict() for b in self.items if b.kind == "will_not_have" and b.status == "violation"],
            "clean": [b.to_dict() for b in self.items if b.kind == "will_not_have" and b.status == "clean"],
        }


# ---------------------------------------------------------------- 关键词

def extract_keywords(item: str) -> List[str]:
    """从一条基线文本提取关键词：文件样 token / 中文片段 / 英文词（去重保序）。"""
    out: List[str] = []
    seen: set = set()
    for m in _FILENAME_TOKEN_RE.findall(item):
        tok = m.strip()
        if tok and tok not in seen:
            seen.add(tok)
            out.append(tok)
    for m in _CJK_RE.findall(item):
        # 中文连续片段直接作为关键词（如“订单数据落盘”）—— 足够长的短语才有区分度
        if m and m not in seen:
            seen.add(m)
            out.append(m)
    for m in _ASCII_WORD_RE.findall(item):
        tok = m.lower()
        if tok.lower() not in seen and tok not in ("the", "and", "for", "with"):
            seen.add(tok)
            out.append(tok)
    return out


# 模式关键词（check_baseline 使用；extract_keywords 保留全量用于调试/测试）
# - will_have    : 文件样 token + 中文片段（自由 ascii 词易误命中，如 json/src/yaml；
#                  仅当无文件样无中文时回退 ascii 长词 >=5，保证纯英文基线可用）
# - will_not_have: 文件样 token + 中文片段 + 中文安全二元组 + ascii（护栏语义，宁多勿漏）
_CJK_STOP_CHARS = "不做与和及或为是的要应该有无处理本任务对于之间并且在只需用以将按照从到都会其因但么呢吧啊被把让给"
_ASCII_NOISY = {"json", "yaml", "yml", "csv", "src", "test", "md", "api", "the", "and",
                "for", "with", "not", "file", "path", "note", "order", "data", "name"}


def _safe_cjk_bigrams(run: str) -> List[str]:
    """中文片段的 2 字窗（不含停用字），用于 will_not_have 的部分命中（如“支付”）。"""
    out: List[str] = []
    for i in range(len(run) - 1):
        w = run[i:i + 2]
        if w and w[0] not in _CJK_STOP_CHARS and w[1] not in _CJK_STOP_CHARS:
            out.append(w)
    return out


def matching_keywords(item: str, mode: str) -> List[str]:
    """按模式返回证据匹配用关键词。mode: will_have | will_not_have。"""
    toks = extract_keywords(item)              # [file..., cjk..., ascii...]
    file_toks = [t for t in toks if "/" in t or ("." in t and t.rsplit(".", 1)[1].isalnum())]
    cjk = [t for t in toks if re.fullmatch(r"[\u4e00-\u9fff]+", t) and len(t) >= 2]
    ascii_w = [t for t in toks if t not in file_toks and t not in cjk]
    out: List[str] = []
    out.extend(file_toks)
    out.extend(cjk)
    if mode == "will_not_have":
        for run in cjk:
            out.extend(_safe_cjk_bigrams(run))
        out.extend(w for w in ascii_w if w.lower() not in _ASCII_NOISY)
    elif not file_toks and not cjk:
        # 纯英文基线回退：仅长词且非噪声
        out.extend(w for w in ascii_w if len(w) >= 5 and w.lower() not in _ASCII_NOISY)
    # 去重保序
    seen: set = set()
    final: List[str] = []
    for k in out:
        if k not in seen:
            seen.add(k)
            final.append(k)
    return final


# ---------------------------------------------------------------- 证据面

def _iter_deliverable_paths(ic: IntegrateContext):
    """交付物证据路径迭代（**只含 executor 真实产出**，排除任务输入回显与模板）。

    - modules/*/src、modules/*/test 下的文件（路径级证据）
    - modules/*/交付说明.md、modules/*/REVIEW.md（文本证据；REVIEW 只取 已做 节）
    - shared/ 下非脚手架样板文件（executor/上游写入的共享数据）
    排除：skeleton.md / contracts/api.yaml / 认知/ / 任务书-*.yaml / contract.yaml（这些是
    任务输入或契约声明，会回显基线文本导致误命中 —— docs 已知限制已标注证据面口径）。
    """
    seen: set = set()
    for mid in ic.module_order:
        mdir = ic.module_dir(mid)
        for sub in ("src", "test"):
            base = mdir / sub
            if not base.is_dir():
                continue
            for p in sorted(base.rglob("*")):
                if not p.is_file() or p.name in _IGNORE_FILES:
                    continue
                rel = p.relative_to(ic.task_root)
                if rel.as_posix() not in seen:
                    seen.add(rel.as_posix())
                    yield rel
        for name in ("交付说明.md", "REVIEW.md"):
            p = mdir / name
            if p.is_file():
                rel = p.relative_to(ic.task_root)
                if rel.as_posix() not in seen:
                    seen.add(rel.as_posix())
                    yield rel
    shared = ic.task_root / "shared"
    if shared.is_dir():
        for p in sorted(shared.rglob("*")):
            if not p.is_file() or p.name in _IGNORE_FILES:
                continue
            if p.name in ("README.md", ".readonly"):
                continue      # 脚手架样板，非交付物
            rel = p.relative_to(ic.task_root)
            if rel.as_posix() not in seen:
                seen.add(rel.as_posix())
                yield rel


def _is_text_file(p: Path) -> bool:
    return p.suffix.lower() in (".json", ".yaml", ".yml", ".csv", ".txt", ".md", ".py",
                                ".sh", ".toml", ".cfg", ".ini", "")


def _read_text(p: Path) -> str:
    try:
        data = p.read_bytes()[:_MAX_READ]
        return data.decode("utf-8", errors="ignore")
    except OSError:
        return ""


def _file_token_hit(token: str, ic: IntegrateContext) -> Optional[str]:
    """文件样 token 精确匹配：任务根 / modules/* / shared / contracts。命中返回匹配相对路径。"""
    # 先去尾部参数/引号等杂质（保守：只按原始 token 尝试）
    cands = [ic.task_root / token.lstrip("./"),
             ic.task_root / "modules" / token.lstrip("./"),
             ic.task_root / "shared" / token.lstrip("./"),
             ic.task_root / "contracts" / token.lstrip("./")]
    for c in cands:
        if c.is_file():
            return c.relative_to(ic.task_root).as_posix()
    # 若包含 mXX/ 前缀，直接在 modules/ 下解析
    m = re.match(r"^(m\d+)/(.*)$", token)
    if m:
        try:
            mdir = ic.module_dir(m.group(1))
            c = mdir / m.group(2)
            if c.is_file():
                return c.relative_to(ic.task_root).as_posix()
        except Exception:
            pass
        return None
    # 末尾可能带自然语言后缀（如 “src/data/orders.json 结构按契约”），尝试截取到扩展名后
    return None


def _collect_text_evidence(ic: IntegrateContext) -> Dict[Path, str]:
    """交付物文本证据面：{相对路径: 文本}（每文件 ≤8KB；REVIEW.md 只取 已做 节）。

    说明：src/test 产物 + 交付说明.md 全文 + REVIEW 已做节 + shared 数据文件 ——
    都是 executor 实际产出/记录的内容；模板与任务书回显文件不入选（防误命中）。
    """
    out: Dict[Path, str] = {}
    for mid in ic.module_order:
        mdir = ic.module_dir(mid)
        for sub in ("src", "test"):
            base = mdir / sub
            if not base.is_dir():
                continue
            for p in sorted(base.rglob("*")):
                if not p.is_file() or p.name in _IGNORE_FILES or p.name in (".gitkeep",):
                    continue
                rel = p.relative_to(ic.task_root)
                if _is_text_file(p):
                    out[rel] = _read_text(p)
        delivery = mdir / "交付说明.md"
        if delivery.is_file():
            out[delivery.relative_to(ic.task_root)] = _read_text(delivery)
        review = mdir / "REVIEW.md"
        if review.is_file():
            done_text = _review_done_text(review)
            if done_text:
                rel = Path("modules") / mdir.name / "REVIEW.md#已做"
                out[rel] = done_text
    shared = ic.task_root / "shared"
    if shared.is_dir():
        for p in sorted(shared.rglob("*")):
            if not p.is_file() or p.name in _IGNORE_FILES:
                continue
            if p.name in ("README.md", ".readonly"):
                continue
            rel = p.relative_to(ic.task_root)
            if _is_text_file(p):
                out[rel] = _read_text(p)
    return out


def _review_done_text(review_path: Path) -> str:
    """REVIEW.md 已做节内容（executor 记录的实际完成条目）。"""
    try:
        from fw_runner.review import read_review
        doc = read_review(review_path)
        return "\n".join(doc.list_done())
    except Exception:
        return ""


# ---------------------------------------------------------------- 对照

class _PathIndex:
    """相对路径索引：小写路径 → 原路径（用于关键词在路径级的匹配）。"""

    def __init__(self, paths: List[Path]) -> None:
        self._lower: Dict[str, Path] = {}
        for p in paths:
            self._lower.setdefault(p.as_posix().lower(), p)
            self._lower.setdefault(p.name.lower(), p)

    def find(self, token: str) -> Optional[str]:
        if "/" in token or "." in token:
            hit = self._file_token_hit_token(token, self._lower)
            if hit:
                return hit
        name = token.lower().replace("\\", "/")
        return self._lower.get(name).as_posix() if name in self._lower else None

    @staticmethod
    def _file_token_hit_token(token: str, lower_map: Dict[str, Path]) -> Optional[str]:
        cand = token.lstrip("./").lower().replace("\\", "/")
        if cand in lower_map:
            return lower_map[cand].as_posix()
        # 模糊：路径尾部匹配（token 是相对路径的子串后缀）
        for key, p in lower_map.items():
            if key.endswith(cand) and len(cand) > 3:
                return p.as_posix()
        return None


def _evidence_for_keyword(kw: str, ic: IntegrateContext,
                          texts: Dict[Path, str],
                          path_index: _PathIndex) -> List[str]:
    """关键词在所有证据面的命中证据列表（至少命中一处才算 matched）。"""
    ev: List[str] = []
    # 1. 路径级（文件样 token 或目录名）
    path_hit = path_index.find(kw)
    if path_hit:
        ev.append(path_hit)
    # 2. 文本级（大小写不敏感）
    k_lower = kw.lower()
    for rel, text in texts.items():
        if k_lower in text.lower():
            ev.append(rel.as_posix())
            if len(ev) >= 5:
                break
    return ev


def check_baseline(ic: IntegrateContext) -> BaselineResult:
    """执行预测基线对照，输出匹配/缺失/违反清单。"""
    task = ic.effective.get("task") or {}
    pb = task.get("prediction_baseline") if isinstance(task, dict) else None
    pb = pb if isinstance(pb, dict) else {}
    will_have = [str(x) for x in (pb.get("will_have") or []) if str(x).strip()]
    will_not_have = [str(x) for x in (pb.get("will_not_have") or []) if str(x).strip()]

    texts = _collect_text_evidence(ic)
    paths = list(texts.keys())
    path_index = _PathIndex(paths)

    items: List[BaselineItem] = []
    for item in will_have:
        kws = matching_keywords(item, "will_have")
        evidence: List[str] = []
        if not kws:
            items.append(BaselineItem(kind="will_have", item=item, status="missing", evidence=[]))
            continue
        for kw in kws:
            ev = _evidence_for_keyword(kw, ic, texts, path_index)
            # 文件样 token：还尝试模块目录相对解析（如 src/data/orders.json 挂在 m01 下）
            if not ev and "/" in kw and not kw.startswith("m"):
                for mid in ic.module_order:
                    c = ic.module_dir(mid) / kw.lstrip("./")
                    if c.is_file():
                        ev.append(c.relative_to(ic.task_root).as_posix())
                        break
            evidence.extend(ev)
        status = "matched" if evidence else "missing"
        items.append(BaselineItem(kind="will_have", item=item, status=status,
                                  evidence=list(dict.fromkeys(evidence))[:8]))

    for item in will_not_have:
        kws = matching_keywords(item, "will_not_have")
        evidence: List[str] = []
        for kw in kws:
            ev = _evidence_for_keyword(kw, ic, texts, path_index)
            evidence.extend(ev)
        status = "violation" if evidence else "clean"
        items.append(BaselineItem(kind="will_not_have", item=item, status=status,
                                  evidence=list(dict.fromkeys(evidence))[:8]))

    result = BaselineResult(ok=(not any(b.status in ("missing", "violation") for b in items)),
                            items=items)
    return result
