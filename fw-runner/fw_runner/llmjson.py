"""llmjson —— LLM JSON 输出的统一解析层（json-repair + Pydantic）。

背景（BUG-20260903 案例，Owner决策）：LLM 输出 JSON 的故障分三类，原先框架用
「prompt 约定 + 正则剥壳 + 手写归一化」三层补丁硬扛，每遇新漂移加一个 if。
本模块收敛为两级标准处理：

  1. 格式病（markdown 围栏 / 尾逗号 / 单引号 / 截断 / 字符串数字）
     → json.loads 直解，失败走 json_repair 修复；
  2. 结构病（字段缺失 / 错型 / 嵌套塞错层）
     → Pydantic model 校验：类型自动 coercion + 精确到字段路径的报错；
  3. 语义漂移（内容塞进错误字段但类型恰好合法）
     → 不属于本层，仍由 model_validator(before) 归一化（结构对齐）+ 提示词兜底。

═══════════════════════════════════════════════════════════════════════════
 四层容错契约（2026-09-04 Owner定图，小澈复查落地）—— 改这条链路前先读这段
═══════════════════════════════════════════════════════════════════════════

  层① Prompt 约束    prompts/split.md            尽力而为，唯一与 LLM 对话的一层
  层② 格式+结构      本模块（repair + Pydantic）  **职责是转换，不是判定**
  层③ 错误回传重试    split.call_split_agent      字段级 errors 回喂 LLM，≤ 1+N 次
  层④ 兜底捞取        fw-split.sh._extract        能捞多少捞多少，但必须留痕

  三条层间铁律（这次的两个 P0 全是层间漏，不是层内 bug）：

  R1 层②→业务 只交接「归一化后的对象」。
     校验函数若返回 bool，coercion 结果就被丢弃 → 下游继续读脏 dict。
     落点：validate_split_json 原地写回；call_split_agent 只认写回后的对象。
     （P0-1 教训：顿号串 deliverables 过校验后按**字符**迭代成 20 多条验收项。）

  R2 层④ 只捞「结构」，不猜「语义」。
     语义字段（id/name/objective/deliverables）空值一律拒，min_length=1 硬约束；
     层④捞出来的东西必须带 meta 标记，不能和层①②的产物混为同一种可信度。

  R3 每层降级必须留痕，静默降级 = 没有降级。
     meta 字段：layer（0=程序直出 1=直解 2=repair 修复 4=兜底/失败）、
               repaired、truncated、candidates/parsed、source（llmjson/inline-fallback/demo/precheck）
     → 由 detail._parse 透传给 call_split_agent，经 on_event 送进 dispatch.jsonl。
     （P0-3 教训：pydantic 未声明依赖 → 外部用户全程走降级路径，日志一个字没有。）

  故障分类（决定"回不回喂"）：
     协议故障（SplitJSONError）   → 层③回喂 LLM，一次 flash，最省 token 的一条路
     基础设施故障（SplitInfraError）→ 不回喂（回喂一万次 dsh 也不会自己装上），快失败 + 真实归因
     上游故障（限流/断网/5xx）      → 复用 drivers.classify_env_error 口径，不另造分类器

  收口进度（见审查报告 P1-5 / P1-7，别以为已经统一了）：
     · auditor 判定解析已于 2026-09-04 迁到本模块 AuditorOutcome + parse_auditor_payload，
       fw-auditor.sh 不再用判定词正则（m02）；executor 的 outcome 解析（ExecutorOutcome）
       仍各自为政，待下一模块（m03）照此样板统一。
     · drivers.ScriptedAgentDriver 用 shell=True 拼命令，占位符未转义（m04）

依赖：pydantic（**硬依赖**，已在 pyproject.toml 声明）、json_repair（可缺省，缺了只丢修复能力）。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Literal, Optional

# 分隔串 → 列表 的统一切分符。只给 dependency_map 的值用（模块 id 不含逗号，切分安全）；
# deliverables 走 SplitBlock._coerce_list，只按顿号/分号切——交付物正文里合法含逗号，
# 按逗号切会把一条验收项拆成两条，语义风险大于收益（刻意不对称）。
_SEP_RE = re.compile(r"[、,，;；]")

try:
    import json_repair as _json_repair
except ImportError:  # pragma: no cover - 缺库时优雅降级（只丢修复能力，不丢校验）
    # ⚠️ 必须捕 ImportError 而非 Exception：宽捕获会把 json_repair 自身的
    # SyntaxError/RecursionError 误吞成「未安装」，静默丢掉第二层的修复半边。
    _json_repair = None

from pydantic import (
    BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator,
)


# ---------------------------------------------------------------------------
# 1. 格式层：文本 → JSON 对象
# ---------------------------------------------------------------------------

def _strip_code_fences(text: str) -> str:
    """去掉 markdown 代码块围栏行（```json / ```）。"""
    return "\n".join(
        ln for ln in text.splitlines() if not ln.strip().startswith("```")
    )


def loads_llm(text: str) -> Optional[Any]:
    """解析 LLM 输出的 JSON 文本：直解 → json_repair 修复 → None。

    返回任意 JSON 值（dict/list/标量）；完全不可解析返回 None（不抛异常，
    由调用方决定报错话术）。需要知道"是第几层捞回来的"用 loads_llm_with_meta。
    """
    obj, _meta = loads_llm_with_meta(text)
    return obj


def loads_llm_with_meta(text: str) -> "tuple[Optional[Any], Dict[str, Any]]":
    """同 loads_llm，但返回 (对象, 解析留痕 meta)。

    分层容错契约（2026-09-04 Owner定的四层图）要求**每次降级留痕**：
    静默降级 = 没有降级（P0-3 就是这么被吞掉的）。
      meta["layer"]    1=直解成功  2=json_repair 修复成功  4=失败兜底
      meta["repaired"] 是否走过修复层（层④捞出来的东西必须带这个标记）
    """
    meta: Dict[str, Any] = {"layer": 4, "repaired": False}
    if not isinstance(text, str) or not text.strip():
        return None, meta
    cleaned = _strip_code_fences(text)
    try:
        meta.update(layer=1, repaired=False)
        return json.loads(cleaned), meta
    except Exception:
        pass
    if _json_repair is not None:
        try:
            repaired = _json_repair.loads(cleaned)
        except Exception:
            return None, meta
        # json_repair 对纯文本会"尽力"返回原文字符串——那不是修复出的 JSON 结构：
        # 结果是 str 且与剥围栏原文一致（或为空）→ 判定不可解析
        if isinstance(repaired, str):
            stripped = cleaned.strip()
            if not repaired or repaired.strip() == stripped:
                return None, meta
        meta.update(layer=2, repaired=True)
        return repaired, meta
    return None, meta


def extract_json_objects(text: str) -> List[Dict[str, Any]]:
    """从混合文本中提取所有平衡的 JSON 对象，逐个尝试直解 + json_repair 修复。

    供 agent 包装脚本（fw-split.sh 等）从 dsh 输出里找拆解 JSON 用。
    只有「成功解析为 dict」的候选才返回——修复失败的候选静默丢弃
    （与旧正则方案行为对齐，但修复成功率大幅提高）。
    需要降级留痕的用 extract_json_objects_with_meta。
    """
    objs, _meta = extract_json_objects_with_meta(text)
    return objs


def extract_json_objects_with_meta(text: str) -> "tuple[List[Dict[str, Any]], Dict[str, Any]]":
    """同 extract_json_objects，附带提取层留痕（候选数 / 是否走过 repair）。"""
    meta: Dict[str, Any] = {"candidates": 0, "parsed": 0, "repaired": False, "truncated": False}
    if not isinstance(text, str) or "{" not in text:
        return [], meta
    cleaned = _strip_code_fences(text)
    cands: List[str] = []
    tail_objs: List[Dict[str, Any]] = []     # 截断尾巴 repair 出来的对象（排在最后）
    i = 0
    while True:
        start = cleaned.find("{", i)
        if start < 0:
            break
        depth, in_str, esc, end = 0, False, False, -1
        for j in range(start, len(cleaned)):
            ch = cleaned[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = j
                    break
        if end < 0:
            # 括号不平衡：疑似输出被截断（max tokens 截尾 / agent 半截吐）。
            # ① 标记留痕；② 继续往里层扫（保住原 salvage 能力）；
            # ③ **把这段未闭合尾巴整段交给 json_repair 补全**——否则"截断可修复"是假的：
            #   外层不闭合就不是候选，只剩内层碎片能被捞出，捞到的还是残缺对象。
            meta["truncated"] = True
            if _json_repair is not None:
                try:
                    salvaged = _json_repair.loads(cleaned[start:])
                except Exception:
                    salvaged = None
                if isinstance(salvaged, dict) and salvaged:
                    tail_objs.append(salvaged)   # 统一放到最后：prefer="last" 时它就是终稿
                    meta["repaired"] = True
                    meta["salvaged_truncated"] = True
            i = start + 1
            continue
        cands.append(cleaned[start:end + 1])
        i = end + 1

    meta["candidates"] = len(cands)
    out: List[Dict[str, Any]] = []
    for c in cands:
        obj, m = loads_llm_with_meta(c)
        if m.get("repaired"):
            meta["repaired"] = True
        if isinstance(obj, dict):
            out.append(obj)
    # 截断尾巴补全出来的对象放最后（agent 通常在末尾给终稿，prefer="last" 会先取它）
    out.extend(tail_objs)
    meta["candidates"] = meta["candidates"] + len(tail_objs)
    meta["parsed"] = len(out)
    return out, meta



# ---------------------------------------------------------------------------
# 2. 结构层：Split 拆解 JSON 的 Pydantic 协议（v2 贪心单块）
# ---------------------------------------------------------------------------

class RemainingAfter(BaseModel):
    """拆完本块后的剩余量估计（允许空 = 收尾块语义：做完即 done）。"""

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    scope: str = ""
    estimate_lines: int = 0

    @field_validator("scope", mode="before")
    @classmethod
    def _coerce_scope(cls, v: Any) -> Any:
        # P0-2（2026-09-04 小澈复查）：LLM 收尾块高频写 "scope": null，
        # 原先只 estimate_lines 有 before-validator → None 被判 "should be a valid
        # string"，整块拆解被拒回人。旧手写校验只查 remaining_after 是 dict，放过过，
        # 这里补齐（scope 空 = 收尾块语义，提示词文档同义）。
        return "" if v is None else v

    @field_validator("estimate_lines", mode="before")
    @classmethod
    def _coerce_lines(cls, v: Any) -> Any:
        # "900" / "约900行" / 900.0 → 900；无法识别 → 0（报表量，宁可降级不炸管道）
        if v is None:
            return 0
        if isinstance(v, bool):
            return 0
        if isinstance(v, int):
            return v
        if isinstance(v, float):
            return int(v)
        s = str(v)
        digits = "".join(ch for ch in s if ch.isdigit())
        return int(digits) if digits else 0


class SplitBlock(BaseModel):
    """贪心单块：一次只拆下一块（prompts/split.md 协议）。

    容错边界（2026-09-04 Owner决策）：类型病 coercion 放行，但关键语义字段
    （id/name/objective 非空、deliverables 非空）硬约束——下游要拿它们建
    子模块目录与任务书，空值不能靠猜。
    """

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    deliverables: List[str] = Field(min_length=1)
    files: List[str] = []

    @field_validator("id", "name", "objective", mode="before")
    @classmethod
    def _str_or_empty(cls, v: Any) -> Any:
        return "" if v is None else v

    @field_validator("deliverables", "files", mode="before")
    @classmethod
    def _coerce_list(cls, v: Any) -> Any:
        if v is None:
            return []
        if isinstance(v, str):
            # LLM 偶尔把列表写成顿号/逗号分隔串 → 拆开（语义级兜底的最小集）
            for sep in ("；", ";", "、"):
                if sep in v:
                    return [p.strip() for p in v.split(sep) if p.strip()]
            return [v.strip()] if v.strip() else []
        return v


class SplitJSON(BaseModel):
    """拆解 JSON 协议：action=split（含 next_block 单块）或 cannot_split（兜底）。"""

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    action: Literal["split", "cannot_split"]
    parent_module: str = ""
    next_block: Optional[SplitBlock] = None
    remaining_after: Optional[RemainingAfter] = None
    dependency_map: Dict[str, List[str]] = {}
    context_from_parent: str = ""
    reason: str = ""

    @model_validator(mode="before")
    @classmethod
    def _require_remaining_after(cls, data: Any) -> Any:
        """缺失 ≠ 收尾（2026-09-04 小澈复查 P0-4：Pydantic 迁移引入的丢活回归）。

        旧手写校验的 REQUIRED_TOP 里有 remaining_after，模型漏写 → "缺少必需字段"
        → SplitJSONError → 回人（响亮失败）。换成 Pydantic 且字段带默认值
        RemainingAfter() 之后，漏写被静默补成 estimate_lines=0 → 这个 0 会写进
        子模块的 remaining_estimate → 出口判定读到 0 → **子模块做完首发块就 done，
        父模块剩下的活凭空消失，且全绿零报错**（静默丢活，比回人严重一档）。

        所以这里显式要求：action=split 时 remaining_after 必须**存在**
        （收尾块也要老实写 {"scope": "", "estimate_lines": 0}，prompts/split.md 同此约定）。
        cannot_split 分支不受影响（它本来不带块）。
        """
        if isinstance(data, dict) and data.get("action") == "split" \
                and "remaining_after" not in data:
            raise ValueError(
                "remaining_after 缺失：收尾块必须显式写 "
                '{\'scope\': "", \'estimate_lines\': 0}，不能用缺省表示"没有剩余"')
        return data

    @field_validator("remaining_after", mode="before")
    @classmethod
    def _empty_ra_to_default(cls, v: Any) -> Any:
        # 显式写了但写成 {} / null → 按收尾块语义补默认（与"压根没写"区分开，见上）
        return {} if v is None else v

    @field_validator("dependency_map", mode="before")
    @classmethod
    def _coerce_depmap(cls, v: Any) -> Any:
        # P0-2（2026-09-04 小澈复查）：值写成裸串 {"m05a": "m04"} 是 LLM 高频写法，
        # 原先只判「外层是不是 dict」，值不 coerce → Dict[str, List[str]] 整块被拒回人。
        # 逐值归一：str 串（含顿号/逗号分号）→ 列表、list 逐项 str、None/非 dict → {}。
        if not isinstance(v, dict):
            return {}
        out: Dict[str, List[str]] = {}
        for key, val in v.items():
            k = str(key).strip()
            if not k:
                continue
            if val is None:
                out[k] = []
            elif isinstance(val, str):
                out[k] = [p.strip() for p in _SEP_RE.split(val) if p.strip()]
            elif isinstance(val, (list, tuple)):
                out[k] = [str(x).strip() for x in val if str(x).strip()]
            else:
                out[k] = [str(val).strip()]
        return out

    @field_validator("context_from_parent", "reason", mode="before")
    @classmethod
    def _str_or_empty2(cls, v: Any) -> Any:
        return "" if v is None else v

    @field_validator("next_block", mode="before")
    @classmethod
    def _empty_block_to_none(cls, v: Any) -> Any:
        # cannot_split 时 LLM 常给 next_block: {} / null → 统一 None
        if isinstance(v, dict) and not v:
            return None
        return v

    @field_validator("parent_module", mode="before")
    @classmethod
    def _default_parent(cls, v: Any) -> Any:
        return "" if v is None else str(v)


def normalize_split_payload(data: Any, mid: str = "") -> Optional[Dict[str, Any]]:
    """结构对齐（语义漂移归一化，移植自 split._normalize_split_json，BUG-20260829）：

    1) 裸 next_block（有 id 无 action）→ 包成 {action:split, parent_module, next_block}
    2) remaining_after / dependency_map / context_from_parent 塞进 next_block 内层 → 提升到顶层
    """
    if not isinstance(data, dict):
        return data
    if "action" not in data and isinstance(data.get("id"), str):
        nb = dict(data)
        data = {"action": "split", "parent_module": str(mid), "next_block": nb}
    nb = data.get("next_block")
    if isinstance(nb, dict):
        for k in ("remaining_after", "dependency_map", "context_from_parent"):
            if k not in data and k in nb:
                data[k] = nb.pop(k) if isinstance(nb, dict) else nb[k]
    return data


def parse_split_json(text: str, mid: str = "") -> Optional[Dict[str, Any]]:
    """一站式：LLM 文本 → 提取候选 → 格式修复 → 归一化 → Pydantic 校验 → dict。

    返回第一个通过协议校验的 dict（action=split 或 cannot_split）；
    全部候选失败返回 None（调用方给出确定性报错）。
    需要"为什么失败 / 第几层捞的"用 parse_split_payload。
    """
    payload, _errors, _meta = parse_split_payload(text, mid)
    return payload


def parse_split_payload(text: str, mid: str = "", *,
                        prefer: Literal["first", "last"] = "first"
                        ) -> "tuple[Optional[Dict[str, Any]], List[str], Dict[str, Any]]":
    """拆解 JSON 的唯一入口（四层容错契约的层②→层③交接面）。

    返回 (归一化 payload, 字段级 errors, 解析留痕 meta)：

      payload  通过校验的**归一化后** dict —— 业务逻辑只能读这个，
               不能回头读原始文本捞出来的那份（P0-1 教训：校验=转换，不是判定）
      errors   人话 + 字段路径的失败原因列表 —— 专门给层③回喂 LLM 用
               （Pydantic 相对手写校验的唯一增量就是这份结构化 errors，不回喂等于白做）
      meta     {"layer": 1|2|4, "repaired": bool, "candidates": n, "truncated": bool}

    prefer：多候选时取「第一个过 schema 的」(first) 还是「最后一个含 action 的」(last)。
      shell 侧历史实现取 last（agent 末尾给终稿）——两种语义在多块输出时结论不同，
      所以收敛到这一个函数、由调用方显式声明，不再各写一份（P1-5）。
    """
    objs, meta = extract_json_objects_with_meta(text)
    meta = dict(meta)
    meta.setdefault("layer", 4)
    if not objs:
        return None, ["输出里找不到可解析的 JSON 对象（可能被截断或整段是自然语言）"], meta
    cands = list(reversed(objs)) if prefer == "last" else objs
    reasons: List[str] = []
    for raw in cands:
        data = normalize_split_payload(raw, mid)
        if data is None:
            continue
        try:
            model = SplitJSON.model_validate(data)
        except ValidationError as exc:
            reasons.extend(_humanize_validation_error(exc))
            continue
        payload = model.model_dump()
        meta["layer"] = 1 if not meta.get("repaired") else 2
        return payload, [], meta
    # 全部候选失败 = 落到第四层兜底也没捞出合法结构
    meta["layer"] = 4
    merged: List[str] = []
    for r in reasons:
        if r not in merged:
            merged.append(r)
    return None, merged or ["候选 JSON 均不符合拆解协议"], meta


# ---------------------------------------------------------------------------
# 3. auditor 判定协议（AuditorOutcome）—— 四层容错契约推广的样板（m02）
# ---------------------------------------------------------------------------
# 设计要点（对齐 Split 的 R1/R3，A4 教训）：
#   · R1  层②只交接「归一化后的对象」—— parse_auditor_payload 返回通过校验的 dict，
#         消费方（fw-auditor.sh / runner）只准读它，不准回头读原始文本捞的脏 dict。
#   · A4  关键语义字段（verdict / passed_count / total_count）缺失→ 留痕拒绝走回人，
#         绝不静默补 0 冒充判定（这是 auditor 判定的根基，不是报表字段）。
#         因此这三个字段**不给默认值**：缺 key → Pydantic "field required" → 拒绝。
#   · 判定词变体（pass/通过/… partial/部分… block/不通过…）归一化到三态 enum；
#         认不出的措辞不改判、原样留给 Pydantic 报错（宁回人不乱猜）。

# 三态判定同义词（值侧归一化；键 → 规范三态）。只收意图清晰的同义措辞，
# 拿不准的宁可留回人——误归一化一个 verdict 比拒绝一个严重得多。
_VERDICT_SYNONYMS: Dict[str, str] = {
    # -> pass
    "pass": "pass", "passed": "pass", "ok": "pass", "green": "pass",
    "通过": "pass", "全部通过": "pass", "无阻塞项": "pass", "可验收": "pass",
    "已通过": "pass", "验收通过": "pass", "完成": "pass",
    # -> partial
    "partial": "partial", "partially": "partial", "部分": "partial",
    "部分通过": "partial", "部分满足": "partial", "部分完成": "partial",
    "部分达成": "partial", "部分验收": "partial", "非全部通过": "partial",
    # -> block
    "block": "block", "blocked": "block", "fail": "block", "failed": "block",
    "不通过": "block", "验收失败": "block", "失败": "block",
    "全部不通过": "block", "未通过": "block",
}

# verdict 值规范化：去掉空白/标点/数字分隔，只留中文/字母，再小写比对。
_VERDICT_CLEAN_RE = re.compile(r"[^\w\u4e00-\u9fff]+")


class AuditorOutcome(BaseModel):
    """auditor 判定 JSON 协议（verdict/passed_count/total_count/remaining_items/evidence）。

    字段容错（coercion，转换不是判定）：
      verdict          判定词变体 → 三态 enum；认不出 → Pydantic 报错拒绝
      passed/total     "3"/"3.0"/3 数字串 → int；负值/非数 → 报错拒绝
      remaining_items / evidence  list；裸串（顿号/分号/逗号分隔）→ 拆列表

    拒绝语义（A4）：verdict / passed_count / total_count **无默认值**，
    模型漏写任一 → field required 报错 → parse_auditor_payload 返回 errors → 回人。
    extra="allow" 使协议 extendable（data_shape 声明），不拦多余字段。
    """

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    verdict: Literal["pass", "partial", "block"]
    passed_count: int = Field(ge=0)
    total_count: int = Field(ge=0)
    remaining_items: List[str] = Field(default_factory=list)
    evidence: List[str] = Field(default_factory=list)

    @field_validator("verdict", mode="before")
    @classmethod
    def _coerce_verdict(cls, v: Any) -> Any:
        if isinstance(v, str):
            key = _VERDICT_CLEAN_RE.sub("", v).lower()
            canon = _VERDICT_SYNONYMS.get(key)
            if canon is not None:
                return canon
        return v  # 认不出 → 原样交给 Literal 判非法（宁回人不乱猜）

    @field_validator("passed_count", "total_count", mode="before")
    @classmethod
    def _coerce_count(cls, v: Any) -> Any:
        # "3" / "3.0" / 3 → int；null / bool / 不可识别 → 拒绝（绝不静默补 0/补 1）。
        if isinstance(v, bool):
            raise ValueError("计数必须是整数，bool 不允许（防静默 True→1）")
        if isinstance(v, int):
            return v
        if isinstance(v, float):
            return int(v)
        if isinstance(v, str):
            s = v.strip()
            try:
                return int(s)
            except ValueError:
                try:
                    return int(float(s))       # "5.0"/"3e0" → 5/3
                except ValueError:
                    # 中缀/单位："约3项" → 取数字（报表，非判定根基，放宽）。
                    digits = "".join(ch for ch in s if ch.isdigit())
                    if digits:
                        return int(digits)
        raise ValueError("计数必须是 >=0 的整数，无法从输入识别的不要硬猜")

    @model_validator(mode="after")
    def _counts_consistent(self) -> "AuditorOutcome":
        # 字段错位防线：passed>total 说明 passed/total 很可能写反/串位 → 拒绝，
        # 不靠猜补（猜等于让一个错判定冒充真的；A4 同类教训）。
        if self.passed_count > self.total_count:
            raise ValueError(
                f"passed_count({self.passed_count}) > total_count({self.total_count})：计数串位，拒绝")
        return self

    @field_validator("remaining_items", "evidence", mode="before")
    @classmethod
    def _coerce_str_list(cls, v: Any) -> Any:
        if v is None:
            return []
        if isinstance(v, str):
            # 裸串（顿号/分号/逗号分隔）→ 拆列表；无分隔符 → 单元素列表
            parts = re.split(r"[、,，;；\n]", v)
            return [p.strip() for p in parts if p.strip()]
        if isinstance(v, (list, tuple)):
            return [str(x).strip() for x in v if str(x).strip()]
        return [str(v).strip()] if str(v).strip() else []


def parse_auditor_json(text: str) -> Optional[Dict[str, Any]]:
    """一站式轻量入口：LLM 文本 → AuditorOutcome 判定 dict；失败 None。

    需要「为什么失败 / 第几层捞的」用 parse_auditor_payload。
    """
    payload, _errors, _meta = parse_auditor_payload(text)
    return payload


def parse_auditor_payload(
    text: str, *, prefer: Literal["first", "last"] = "last"
) -> "tuple[Optional[Dict[str, Any]], List[str], Dict[str, Any]]":
    """auditor 判定解析的唯一入口（四层容错契约层②→层③/回人交接面）。

    返回 (归一化 payload, 字段级 errors, 解析留痕 meta)：
      payload   通过校验的**归一化后**判定 dict —— 业务只准读这个（R1）。
                None = 无候选通过（格式病+结构病都捞不回来）→ 走回人/parse_failed。
      errors    人话 + 字段路径失败原因（层③回喂 LLM / 回人留痕用）
      meta      {"layer": 1|2|4, "repaired", "candidates", "parsed", "truncated"}
                 layer 回答「第几层捞回」：1=直解 2=repair 4=失败兜底。
    """
    objs, meta = extract_json_objects_with_meta(text)
    meta = dict(meta)
    meta.setdefault("layer", 4)
    if not objs:
        return None, ["auditor 输出里找不到可解析的判定 JSON（可能被截断或整段是自然语言）"], meta
    cands = list(reversed(objs)) if prefer == "last" else objs
    reasons: List[str] = []
    for raw in cands:
        if not isinstance(raw, dict):
            continue
        try:
            model = AuditorOutcome.model_validate(raw)
        except ValidationError as exc:
            reasons.extend(_humanize_validation_error(exc))
            continue
        payload = model.model_dump()
        meta["layer"] = 1 if not meta.get("repaired") else 2
        return payload, [], meta
    # 全部候选失败 = 第四层兜底也没捞出合法判定结构
    meta["layer"] = 4
    merged: List[str] = []
    for r in reasons:
        if r not in merged:
            merged.append(r)
    return None, merged or ["候选 JSON 均不符合 auditor 判定协议"], meta


def _humanize_validation_error(exc: ValidationError) -> List[str]:
    """Pydantic 错误 → 一行一条的字段路径话术（层③回喂 LLM 的正文）。"""
    out: List[str] = []
    for e in exc.errors():
        loc = ".".join(str(p) for p in e.get("loc", ())) or "(root)"
        out.append(f"{loc}: {e.get('msg')}（收到 {e.get('input')!r}）")
    return out

