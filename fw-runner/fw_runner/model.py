"""fw-runner 数据模型：运行配置 / 模块规格 / 审计判定 / 轮次结果 / 运行结果。

与上游对接（复用已审计产物）：
- fw-protocol validate_file().effective —— 默认值补全后的任务书（runner 的调度唯一事实源）
- fw-scaffold 生成的 v2 目录树 —— modules/mXX-<名>/（REVIEW.md 键值行、contract.yaml、
  任务书-mXX.yaml、交付说明.md）、总日志三件套（dispatch.jsonl / integration.jsonl / 快照.json）

设计铁律（v0.4 / 三权分立）：executor 永不自定验收标准；auditor 只判不写执行
（auditor 判定由 outcome 带回，runner 统一把机器可解析状态键写回 REVIEW.md —— 单一写者，
配合 fs 原子写，无需外部锁）；executor/auditor 只写内容小节（已做/交接/交付说明）。
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# REVIEW.md 状态机合法值（与 fw-scaffold 模板一致）
MODULE_STATUS_OPTIONS = ("pending", "running", "needs_review", "blocked", "done", "needs_human", "split")
ROOT_CAUSES = ("self", "upstream", "contract", "stall", "agent_error", "")
VERDICTS = ("pass", "partial", "block")


def now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass
class ModuleSpec:
    """单个模块的调度规格（从 effective 任务书 + 脚手架目录解析）。"""

    id: str
    name: str
    layer: int
    objective: str
    dependencies: List[str]
    dir: Path            # 模块目录（绝对路径）
    review_path: Path    # REVIEW.md
    contract_path: Path  # contract.yaml
    book_path: Path      # 任务书-mXX.yaml
    delivery_path: Path  # 交付说明.md

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "layer": self.layer,
            "objective": self.objective, "dependencies": list(self.dependencies),
            "dir": str(self.dir),
        }


@dataclass
class RunConfig:
    """执行期配置（effective runtime 默认值 + CLI 覆盖 + 模式开关）。"""

    max_parallel: int = 3
    executor_max_rounds: int = 5
    retry_before_switch: int = 2
    max_executor_switches: int = 1
    enable_split: bool = True           # 是否启用自动拆分（v1.0）
    split_max_depth: int = 2            # 最大拆分深度，防无限递归
    split_min_deliverables: int = 2     # 交付物 ≤ N 的不拆（已是叶子）
    retry_remaining_threshold: int = 2  # 剩余交付物 ≤ N 项 → 续做；>N → split（v2 贪心判定，partial 粒度）
    split_exit_threshold: int = 1000    # 出口判定（杰哥 2026-08-26 拍板试点）：剩余行数 ≤ N → 收官/final 续做，>N → split
    audit_require_evidence: bool = True  # BUG-002a（2026-08-25）：auditor pass 必须带证据等级（L1/L2），L3 无实证 → 回人
    max_partial_rounds: int = 5         # 连续 partial 超 N 次 → 回人（防死循环兜底）
    split_merge_after_fails: int = 3    # 子模块连续失败次数达阈值合并回父
    enable_fallback_model: bool = True  # 是否启用 pro 兜底
    fallback_model: str = "pro"         # 兜底模型（仅当前叶子模块）
    model_tiers: List[str] = field(default_factory=lambda: ["flash", "pro"])
    end_gate: str = "auto"              # auto | always
    heartbeat_n_rounds: int = 2         # 连续 N 轮无实质产出 → 判静默卡死
    checkpoint_every: int = 1           # 每 N 模块完成写一次快照
    mode: str = "speed_first"           # speed_first | cost_first（模式开关，完整策略见需求7）
    models: Dict[str, str] = field(default_factory=dict)
    overrides: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "max_parallel": self.max_parallel,
            "executor_max_rounds": self.executor_max_rounds,
            "retry_before_switch": self.retry_before_switch,
            "max_executor_switches": self.max_executor_switches,
            "enable_split": self.enable_split,
            "split_max_depth": self.split_max_depth,
            "split_min_deliverables": self.split_min_deliverables,
            "retry_remaining_threshold": self.retry_remaining_threshold,
            "split_exit_threshold": self.split_exit_threshold,
            "audit_require_evidence": self.audit_require_evidence,
            "max_partial_rounds": self.max_partial_rounds,
            "split_merge_after_fails": self.split_merge_after_fails,
            "enable_fallback_model": self.enable_fallback_model,
            "fallback_model": self.fallback_model,
            "model_tiers": list(self.model_tiers),
            "end_gate": self.end_gate,
            "heartbeat_n_rounds": self.heartbeat_n_rounds,
            "checkpoint_every": self.checkpoint_every,
            "mode": self.mode,
            "models": dict(self.models),
            "overrides": dict(self.overrides),
        }
        return d


@dataclass
class ModuleAgentState:
    """单模块执行期状态（跑在当前 executor 的轮次/打回/切换/心跳计数）。

    block_count 在换 executor 时清零（新 executor 获得 retry_before_switch 次耐心）；
    block_total 全程累计（供回人报告）。executor_round/auditor_round 为模块级累计。
    """

    executor_round: int = 0
    auditor_round: int = 0
    executor_id: str = ""            # E1/E2/...（runner 统一分配）
    executor_switches: int = 0
    block_count: int = 0             # 当前 executor 被 auditor 打回次数（换人清零）
    block_total: int = 0             # 累计打回次数（回人报告用）
    stall_count: int = 0             # 连续无实质产出轮数
    root: str = ""                   # 最近判定根因：self|upstream|contract|stall|agent_error
    reason: str = ""                 # 最近判定原因文本
    last_verdict: str = ""           # pass | partial | block
    split_depth: int = 0             # 拆分深度（顶层=0，每拆一层 +1）
    parent_module: str = ""          # 父模块 id（顶层模块为空）
    child_modules: List[str] = field(default_factory=list)  # 子模块 id 列表
    partial_count: int = 0           # 连续 partial 次数（可逆用）
    aggregated: bool = False         # 是否已聚合收敛
    model_tier: int = 0              # 当前模型档位（0=flash, 1=pro）
    tokens_used: int = 0             # BUG-004：本模块累计 token 消耗（executor+auditor 各轮回填）
    final_round: bool = False        # 出口判定 final 续做轮标记（remaining 静态后防死循环：final_block 续做一轮即收官）
    started_at: str = ""
    ended_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "executor_round": self.executor_round,
            "auditor_round": self.auditor_round,
            "executor_id": self.executor_id,
            "executor_switches": self.executor_switches,
            "block_count": self.block_count,
            "block_total": self.block_total,
            "stall_count": self.stall_count,
            "root": self.root,
            "reason": self.reason,
            "last_verdict": self.last_verdict,
            "split_depth": self.split_depth,
            "parent_module": self.parent_module,
            "child_modules": list(self.child_modules),
            "partial_count": self.partial_count,
            "aggregated": self.aggregated,
            "model_tier": self.model_tier,
            "tokens_used": self.tokens_used,
            "final_round": self.final_round,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ModuleAgentState":
        s = cls()
        for k in ("executor_round", "auditor_round", "executor_switches",
                  "block_count", "block_total", "stall_count",
                  "split_depth", "partial_count", "model_tier"):
            if isinstance(d.get(k), int):
                setattr(s, k, d[k])
        s.executor_id = str(d.get("executor_id") or "")
        s.parent_module = str(d.get("parent_module") or "")
        if isinstance(d.get("child_modules"), list):
            s.child_modules = [str(x) for x in d["child_modules"]]
        if isinstance(d.get("aggregated"), bool):
            s.aggregated = d["aggregated"]
        s.root = str(d.get("root") or "")
        s.reason = str(d.get("reason") or "")
        s.last_verdict = str(d.get("last_verdict") or "")
        s.tokens_used = int(d.get("tokens_used") or 0)
        if isinstance(d.get("final_round"), bool):
            s.final_round = d["final_round"]
        s.started_at = str(d.get("started_at") or "")
        s.ended_at = str(d.get("ended_at") or "")
        return s


class RunState:
    """一次运行的可变状态（内存态；checkpoint 快照是其持久化投影）。

    status 枚举：running | complete | needs_human | stopped | integration_failed
    modules[id] 枚举：pending | running | done | needs_human
    """

    def __init__(self) -> None:
        self.run_id: str = ""
        self.status: str = "running"
        self.cause: str = ""
        self.modules: Dict[str, str] = {}
        self.failure_counts: Dict[str, int] = {}
        self.per_module: Dict[str, ModuleAgentState] = {}
        self.needs_human: List[str] = []
        self.completed_order: List[str] = []
        self.budget_used_tokens: int = 0
        self.last_seq: int = 0
        self.integration: Dict[str, Any] = {}

    def ensure(self, mid: str) -> ModuleAgentState:
        if mid not in self.per_module:
            self.per_module[mid] = ModuleAgentState()
        return self.per_module[mid]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "cause": self.cause,
            "modules": dict(self.modules),
            "failure_counts": dict(self.failure_counts),
            "per_module": {k: v.to_dict() for k, v in self.per_module.items()},
            "needs_human": list(self.needs_human),
            "completed_order": list(self.completed_order),
            "budget_used_tokens": self.budget_used_tokens,
            "last_seq": self.last_seq,
            "integration": dict(self.integration),
        }


@dataclass
class DriverOutcome:
    """一次 agent 轮次的机器可解析结果（由驱动返回；auditor 判定四段：verdict/root/confidence/reason）。

    status:  ok | interrupted | error
    substance: executor 是否产出实质进展（None=由 runner 用 REVIEW 指纹自动判定）
    """

    status: str = "ok"
    verdict: str = ""            # auditor: pass | block（executor 忽略）
    root: str = ""               # auditor/升级链根因
    confidence: float = 0.0      # auditor 置信度 0-1
    reason: str = ""             # 判定原因 / 失败原因文本
    blocker: str = ""            # auditor 列出的具体 blocker（机器可解析）
    substance: Optional[bool] = None
    tokens: int = 0              # 本轮 token 消耗估计（dsh token-meter 对接钩子；默认 0）
    passed_count: int = 0        # 已通过的交付物数量（partial 判定用）
    total_count: int = 0         # 交付物总数量
    remaining_items: List[str] = field(default_factory=list)  # 未通过的交付物列表
    remaining_lines: Optional[int] = None  # executor 出口自报：本轮做完后剩余行数（≥0；None=未报）
    # BUG-002a（2026-08-25）：auditor 证据等级——L1=命令实跑 L2=内容取证 L3=静态推演
    # 默认 L3（保守）：未声明验收依据的 outcome 一律视为无实证，L3+pass 在 runner 侧强制回人
    evidence_level: str = "L3"
    evidence: List[str] = field(default_factory=list)  # 验收依据清单（读了/跑了什么）
    # v1.3（2026-08-25）：人工验收项（check=manual，框架验收不出来，交人+外部AI）
    human_pending: List[str] = field(default_factory=list)
    detail: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, d: Mapping[str, Any]) -> "DriverOutcome":
        o = cls()
        o.status = str(d.get("status") or "ok")
        o.verdict = str(d.get("verdict") or "")
        o.root = str(d.get("root") or "")
        try:
            o.confidence = float(d.get("confidence") or 0.0)
        except (TypeError, ValueError):
            o.confidence = 0.0
        o.reason = str(d.get("reason") or "")
        o.blocker = str(d.get("blocker") or "")
        o.tokens = int(d.get("tokens") or 0)
        o.passed_count = int(d.get("passed_count") or 0)
        o.total_count = int(d.get("total_count") or 0)
        if isinstance(d.get("remaining_items"), list):
            o.remaining_items = [str(x) for x in d["remaining_items"]]
        if "remaining_lines" in d and d.get("remaining_lines") is not None:
            try:
                o.remaining_lines = int(d["remaining_lines"])
            except (TypeError, ValueError):
                o.remaining_lines = None
        o.evidence_level = str(d.get("evidence_level") or "L3").strip().upper()
        if isinstance(d.get("evidence"), list):
            o.evidence = [str(x) for x in d["evidence"]]
        if isinstance(d.get("human_pending"), list):
            o.human_pending = [str(x) for x in d["human_pending"]]
        if "substance" in d:
            o.substance = bool(d["substance"])
        if isinstance(d.get("detail"), dict):
            o.detail = dict(d["detail"])
        return o


@dataclass
class RunnerResult:
    """一次 fw-runner run 的结果（机器可解析，供 CLI/tests/auditor 消费）。"""

    ok: bool
    status: str                  # complete | needs_human | stopped | interrupted | integration_failed
    exit_reason: str
    run_id: str
    task_root: Path
    checkpoint: Path
    completed: List[str]
    needs_human: List[str]
    failed: List[str]            # 曾失败（打回/换人/卡死）的模块
    tokens_used: int
    duration_s: float
    seq_events: int
    config: Dict[str, Any]
    modules: Dict[str, Any]
    integration: Dict[str, Any]
    events: List[Dict[str, Any]] = field(default_factory=list)
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status,
            "exit_reason": self.exit_reason,
            "run_id": self.run_id,
            "task_root": str(self.task_root),
            "checkpoint": str(self.checkpoint),
            "completed": self.completed,
            "needs_human": self.needs_human,
            "failed": self.failed,
            "tokens_used": self.tokens_used,
            "duration_s": round(self.duration_s, 3),
            "seq_events": self.seq_events,
            "config": self.config,
            "modules": self.modules,
            "integration": self.integration,
            "events": self.events,
            "payload": self.payload,
        }
