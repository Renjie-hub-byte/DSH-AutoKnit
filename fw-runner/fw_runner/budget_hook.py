"""预算闸门对接钩子（需求5 主体归 fw-budget；本模块只留闸门逻辑与记账入口）。

设计定位（v0.4）：token **汇总**归 dsh token-meter（底部免费能力），框架只做**闸门逻辑**
（warn_at 70% 预警 / stop_at 100% 硬停 / per_module_max_tokens 单模块上限）。

实现边界（诚实标注）：
- BudgetGate.check() 的 70%/100%/单模块上限**比例计算逻辑已实现并可单测**；
- 但 token 来源 = driver outcome.tokens（默认 0），dsh token-meter 的跨会话统计
  绑定由 fw-budget 轮接入；本轮默认 NullBudgetGate 永不触发 warn/stop，保证
  runner 主循环不被未就绪的计量卡住。
- runner 在每个 checkpoint / 每模块结束调用 gate.check()：warn → 事件 budget.warn
  （含各模块消耗排行，供 fw-budget 消费）；stop → 硬停（快照 + 抛人，信息完备）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class BudgetStatus:
    """一次闸门检查结果（机器可解析）。"""

    used: int
    max_tokens: int
    warn_at: float
    stop_at: float
    per_module_max_tokens: Optional[int]
    per_module: Dict[str, int] = field(default_factory=dict)
    warned: bool = False
    stop: bool = False
    message: str = ""

    @property
    def ratio(self) -> float:
        if self.max_tokens <= 0:
            return 0.0
        return self.used / self.max_tokens

    def to_dict(self) -> Dict:
        return {
            "used": self.used, "max_tokens": self.max_tokens,
            "ratio": round(self.ratio, 4), "warn_at": self.warn_at, "stop_at": self.stop_at,
            "per_module_max_tokens": self.per_module_max_tokens,
            "per_module": dict(self.per_module), "warned": self.warned,
            "stop": self.stop, "message": self.message,
        }


class BudgetGate:
    """闸门钩子：记账 + 比例计算 + warn/stop 判定（token 源由调用方喂 record）。"""

    def __init__(self, max_tokens: int = 1_000_000, warn_at: float = 0.7,
                 stop_at: float = 1.0, per_module_max_tokens: Optional[int] = None) -> None:
        self.max_tokens = int(max_tokens or 1_000_000)
        self.warn_at = float(warn_at if warn_at is not None else 0.7)
        self.stop_at = float(stop_at if stop_at is not None else 1.0)
        self.per_module_max_tokens = per_module_max_tokens or self.max_tokens
        self.used = 0
        self.per_module: Dict[str, int] = {}

    def record(self, module_id: str, tokens: int) -> None:
        tokens = max(0, int(tokens or 0))
        self.used += tokens
        self.per_module[module_id] = self.per_module.get(module_id, 0) + tokens

    def check(self) -> BudgetStatus:
        """比例计算：70% 预警（不停机）；100% 或单模块超限 → 硬停。"""
        st = BudgetStatus(
            used=self.used, max_tokens=self.max_tokens,
            warn_at=self.warn_at, stop_at=self.stop_at,
            per_module_max_tokens=self.per_module_max_tokens,
            per_module=dict(self.per_module),
        )
        if self.used >= self.max_tokens * self.stop_at:
            st.stop = True
            st.warned = True
            st.message = (f"预算硬停：已用 {self.used} >= max_tokens*stop_at"
                          f"({self.max_tokens}*{self.stop_at})")
            return st
        if self.used >= self.max_tokens * self.warn_at:
            st.warned = True
            st.message = f"预算预警：已用 {self.used}（{st.ratio:.1%}）>= 70% warn_at"
            return st
        # 单模块上限：任一模块超限 → 硬停（防失控模块吃光全局）
        if self.per_module_max_tokens and self.per_module_max_tokens > 0:
            for mid, used in self.per_module.items():
                if used >= self.per_module_max_tokens:
                    st.stop = True
                    st.warned = True
                    st.message = f"单模块超限：{mid} 已用 {used} >= per_module_max_tokens({self.per_module_max_tokens})"
                    break
        return st

    def ranking(self) -> List[Dict]:
        """各模块消耗排行（warn 信息用）。"""
        return sorted(
            ({"module": mid, "tokens": used} for mid, used in self.per_module.items()),
            key=lambda x: x["tokens"], reverse=True,
        )


class NullBudgetGate(BudgetGate):
    """安全默认：只记账不触发 warn/stop（token 源未接 dsh token-meter 时用）。"""

    def check(self) -> BudgetStatus:
        return BudgetStatus(
            used=self.used, max_tokens=self.max_tokens,
            warn_at=self.warn_at, stop_at=self.stop_at,
            per_module_max_tokens=self.per_module_max_tokens,
            per_module=dict(self.per_module),
            warned=False, stop=False,
            message="NullBudgetGate：dsh token-meter 未接入（fw-budget 轮启用），不触发闸门",
        )


def null_gate_from_effective(effective_budget: Optional[Dict]) -> NullBudgetGate:
    """从 effective.budget 构造空闸门（只记账不触发；fw-budget 轮在此接 dsh token-meter）。"""
    b = effective_budget or {}
    return NullBudgetGate(
        max_tokens=int(b.get("max_tokens") or 1_000_000),
        warn_at=float(b.get("warn_at") or 0.7),
        stop_at=float(b.get("stop_at") or 1.0),
        per_module_max_tokens=b.get("per_module_max_tokens"),
    )
