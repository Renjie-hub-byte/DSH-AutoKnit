"""待决策信息展示模块。

从 human_pending / 事件流 / 快照中拼出"待决策"条目，并把每条归类到框架约定的
四类待决策类型之一：

  * ``auditor_reject``  —— auditor 连续打回
  * ``split_ambiguity`` —— split 歧义
  * ``external_request``—— 外部信息请求
  * ``end_gate``        —— end-gate 结束闸门

每条待决策都挂上预定义选项 ``A/B/C/D``（``STANDARD_OPTIONS``），供真人面板选用。

设计要点：
  * **自包含**：本模块不依赖 m03 父包的其它模块，纯标准库实现，仅对齐
    ``data_contract`` 的共享枚举与存储布局，可在独立子模块目录内直接单测，
    也可在聚合后的 ``autoknit_panel`` 包内工作。
  * **形状无关**：对快照(dict/对象)、事件(dict/对象/字符串)、human_pending
    (dict/对象/字符串) 均做容错归一化，字段名有多种候选（见 ``_field``）。
  * **不调任何 LLM**：判定全部走代码/文件读写。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

# ---------------------------------------------------------------------------
# 与 data_contract.shared_enums 对齐的枚举（全模块唯一事实源，禁止自定义）
# ---------------------------------------------------------------------------
STAGES: Tuple[str, ...] = ("planning", "exec", "audit", "split", "idle")
ROLES: Tuple[str, ...] = ("planner", "executor", "auditor")
HUMAN_CHOICES: Tuple[str, ...] = ("A", "B", "C", "D", "text")
STANDARD_OPTIONS: Tuple[str, ...] = ("A", "B", "C", "D")

# 四类待决策类型（唯一合法集合）
DECISION_KINDS: Tuple[str, ...] = (
    "auditor_reject",    # auditor 连续打回
    "split_ambiguity",   # split 歧义
    "external_request",  # 外部信息请求
    "end_gate",          # end-gate 结束闸门
)

KIND_LABELS: Dict[str, str] = {
    "auditor_reject": "auditor 连续打回",
    "split_ambiguity": "split 歧义",
    "external_request": "外部信息请求",
    "end_gate": "end-gate 结束闸门",
}

DEFAULT_STAGE: str = STAGES[0]

# 打回次数达到该值即判定为"连续打回"
REJECT_STREAK_THRESHOLD: int = 2

# ---------------------------------------------------------------------------
# 标记词表：用于从自由文本/事件类型中推断待决策类型
# ---------------------------------------------------------------------------
_REJECT_MARKERS: Tuple[str, ...] = (
    "reject", "rejected", "rework", "redo", "audit_reject", "打回", "返工",
)
_SPLIT_MARKERS: Tuple[str, ...] = ("split", "歧义", "ambigu", "ambiguous")
_EXTERNAL_MARKERS: Tuple[str, ...] = (
    "external", "info", "inform", "outside", "请求", "needs_human", "human", "外部", "信息",
)
_ENDGATE_MARKERS: Tuple[str, ...] = (
    "end_gate", "endgate", "end-gate", "final_review", "闸门", "完成闸门",
)


# ---------------------------------------------------------------------------
# 归一化工具
# ---------------------------------------------------------------------------
def _as_dict(value: Any) -> Dict[str, Any]:
    """把 dict / 有 __dict__ 的对象 / 字符串 统一成 dict，便于字段访问。"""
    if isinstance(value, dict):
        return value
    if hasattr(value, "__dict__") and not isinstance(value, str):
        return dict(vars(value))
    if isinstance(value, str):
        return {"message": value, "text": value}
    return {}


def _field(obj: Any, *names: str, default: Any = None) -> Any:
    """在 dict/对象上按候选字段名依次取第一个非空值。"""
    data = _as_dict(obj)
    for name in names:
        if name in data and data[name] is not None and data[name] != "":
            return data[name]
    return default


def _contains_any(text: Optional[str], markers: Sequence[str]) -> bool:
    if not text:
        return False
    lowered = str(text).lower()
    return any(marker in lowered for marker in markers)


def kind_label(kind: str) -> str:
    """返回待决策类型的中文标签；未知类型回退原文。"""
    return KIND_LABELS.get(kind, kind)


def standard_options() -> List[str]:
    """预定义选项 A/B/C/D（面板渲染用）。"""
    return list(STANDARD_OPTIONS)


# ---------------------------------------------------------------------------
# 待决策条目模型
# ---------------------------------------------------------------------------
@dataclass
class PendingDecision:
    """一条待真人决策的信息。

    Attributes:
        kind: 待决策类型（DECISION_KINDS 之一）。
        label: 类型的中文展示标签。
        message: 给真人看的说明文本。
        module_id: 关联模块；未知为 None。
        options: 预定义选项（默认 A/B/C/D）。
        needs_human: 是否必须等真人回复（True=阻塞）。
        requester: 提出请求的角色（auditor/split/runner 等）。
        raw: 原始来源条目（保留便于排查）。
    """

    kind: str
    message: str
    label: str = field(default="")
    module_id: Optional[str] = None
    options: Tuple[str, ...] = STANDARD_OPTIONS
    needs_human: bool = True
    requester: Optional[str] = None
    raw: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not self.label:
            self.label = kind_label(self.kind)

    def to_dict(self) -> Dict[str, Any]:
        """面板载荷用 dict 表示（data_shape 对齐）。"""
        return {
            "kind": self.kind,
            "label": self.label,
            "message": self.message,
            "module_id": self.module_id,
            "options": list(self.options),
            "needs_human": self.needs_human,
            "requester": self.requester,
        }


# ---------------------------------------------------------------------------
# 事件流推断
# ---------------------------------------------------------------------------
def _event_role(event: Any) -> Optional[str]:
    role = _field(event, "role", "event_role", "from_role")
    return role if role in ROLES else None


def _event_module(event: Any) -> Optional[str]:
    return _field(event, "module", "module_id", "mid")


def _event_text(event: Any) -> str:
    return str(
        _field(
            event,
            "message",
            "text",
            "reason",
            "description",
            "event_type",
            "kind",
            default="",
        )
    )


def _is_reject_event(event: Any) -> bool:
    outcome = str(_field(event, "outcome", "result", "status", default=""))
    text = _event_text(event)
    return _contains_any(outcome, _REJECT_MARKERS) or _contains_any(text, _REJECT_MARKERS)


def _needs_human_flag(event: Any) -> bool:
    val = _field(event, "needs_human", "needsHuman", "wait_human", default=False)
    return bool(val)


def _scan_events(events: Optional[Iterable[Any]]) -> List[PendingDecision]:
    """从事件流中推断待决策条目。

    * auditor_reject：连续出现 >= ``REJECT_STREAK_THRESHOLD`` 次 auditor 打回事件。
    * split_ambiguity：split 相关且标记歧义的事件。
    * end_gate / external_request：needs_human 事件按标记归类。
    """
    result: List[PendingDecision] = []
    events = list(events or [])

    # --- auditor 连续打回 ---
    streak = 0
    for ev in events:
        role = _event_role(ev)
        if role == "auditor" and _is_reject_event(ev):
            streak += 1
        else:
            streak = 0
        if streak >= REJECT_STREAK_THRESHOLD:
            result.append(
                PendingDecision(
                    kind="auditor_reject",
                    message="auditor 连续打回，需要真人裁定如何处理该模块",
                    module_id=_event_module(ev),
                    requester="auditor",
                    raw=ev,
                )
            )
            streak = 0  # 每轮连续打回只生成一条，避免刷屏

    # --- 其余 needs_human 事件按类型标记归类 ---
    for ev in events:
        if not _needs_human_flag(ev):
            continue
        text = _event_text(ev)
        kind_field = str(_field(ev, "kind", "decision_kind", default="")).lower()
        if kind_field in DECISION_KINDS:
            kind = kind_field
        elif _contains_any(text, _SPLIT_MARKERS) or _contains_any(kind_field, _SPLIT_MARKERS):
            kind = "split_ambiguity"
        elif _contains_any(text, _ENDGATE_MARKERS) or _contains_any(kind_field, _ENDGATE_MARKERS):
            kind = "end_gate"
        else:
            kind = "external_request"
        result.append(
            PendingDecision(
                kind=kind,
                message=text or KIND_LABELS[kind],
                module_id=_event_module(ev),
                requester=_event_role(ev),
                raw=ev,
            )
        )
    return result


def _scan_human_pending(human_pending: Optional[Iterable[Any]]) -> List[PendingDecision]:
    """从 human_pending 列表推断待决策条目（逐条归一化）。"""
    result: List[PendingDecision] = []
    for item in human_pending or []:
        data = _as_dict(item)
        if not _needs_human_flag(data) and not data:
            continue
        kind_field = str(_field(data, "kind", "decision_kind", "type", default="")).lower()
        text = str(
            _field(data, "message", "text", "reason", "description", default="")
        )
        if kind_field in DECISION_KINDS:
            kind = kind_field
        elif _contains_any(text, _REJECT_MARKERS):
            kind = "auditor_reject"
        elif _contains_any(text, _SPLIT_MARKERS):
            kind = "split_ambiguity"
        elif _contains_any(text, _ENDGATE_MARKERS):
            kind = "end_gate"
        else:
            kind = "external_request"
        result.append(
            PendingDecision(
                kind=kind,
                message=text or KIND_LABELS[kind],
                module_id=_field(data, "module", "module_id", "mid"),
                requester=_field(data, "requester", "from_role", default="human"),
                raw=item,
            )
        )
    return result


def _dedupe(decisions: List[PendingDecision]) -> List[PendingDecision]:
    """按 (kind, module_id) 去重，保留先出现的条目。"""
    seen = set()
    result: List[PendingDecision] = []
    for d in decisions:
        key = (d.kind, d.module_id)
        if key in seen:
            continue
        seen.add(key)
        result.append(d)
    return result


# ---------------------------------------------------------------------------
# 对外主入口
# ---------------------------------------------------------------------------
def classify_pending(
    snapshot: Optional[Any] = None,
    events: Optional[Iterable[Any]] = None,
    human_pending: Optional[Iterable[Any]] = None,
) -> List[PendingDecision]:
    """综合快照/事件流/human_pending，拼出待决策条目列表。

    Args:
        snapshot: 运行状态快照（dict 或 Snapshot 对象），可为 None。
        events: 事件流（dict/Event 列表），可为 None。
        human_pending: human_pending 列表（需真人决策的条目），可为 None。

    Returns:
        按 DECISION_KINDS 中类型顺序去重后的 PendingDecision 列表。
    """
    collected: List[PendingDecision] = _scan_events(events)
    collected.extend(_scan_human_pending(human_pending))

    # 快照补充：stage == idle 且有挂起条目，但无具体类型时，兜底为 end-gate 闸门
    if snapshot is not None:
        snap = _as_dict(snapshot)
        stage = str(_field(snap, "stage", "current_stage", default=DEFAULT_STAGE)).lower()
        if stage == "idle" and not any(d.kind == "end_gate" for d in collected):
            collected.append(
                PendingDecision(
                    kind="end_gate",
                    message="任务已进入 idle 收尾，等待真人确认是否通过 end-gate",
                    requester="runner",
                    raw=snapshot,
                )
            )

    return _dedupe(collected)


def build_pending_decision(
    snapshot: Optional[Any] = None,
    events: Optional[Iterable[Any]] = None,
    human_pending: Optional[Iterable[Any]] = None,
) -> Dict[str, Any]:
    """把待决策信息拼成面板状态块（可并入 dsh.panel.state 载荷）。

    Returns:
        ``{"blocked": bool, "count": int, "items": [PendingDecision.to_dict(), ...]}``
    """
    decisions = classify_pending(snapshot=snapshot, events=events, human_pending=human_pending)
    return {
        "blocked": bool(decisions),
        "count": len(decisions),
        "items": [d.to_dict() for d in decisions],
    }
