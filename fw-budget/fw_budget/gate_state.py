"""预算闸门重建：从持久化状态（task.yaml budget + token 记账源）构造**真实** BudgetGate。

背景（诚实标注）：fw-runner（round_004 已审计）的 `--resume-from-checkpoint` 会恢复
`state.budget_used_tokens`（快照），但**不会**把累计消耗灌回内存态 BudgetGate（闸门从 0
重新计）。这意味着直接 resume 时，若预算不变，闸门会"失忆"重新计满一轮。本模块负责
在 resume 前用记账源（事件流本地账本 / dsh token-meter）把历史累计消耗 `record` 回
BudgetGate —— 这是 fw-budget 对 runner 钩子的**适配补充**，不改动 fw-runner 已审计代码。
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Mapping, Optional

# 复用 fw-runner（已审计）的 BudgetGate；fw-protocol 的 validate_file 提供 effective 预算默认值
import sys as _sys
_FW1 = Path(__file__).resolve().parent.parent.parent
for _d in ("fw-runner", "fw-protocol"):
    _p = str((_FW1 / _d).resolve())
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

from fw_protocol import validate_file  # noqa: E402
from fw_runner.budget_hook import BudgetGate  # noqa: E402

from .meter import TokenMeter  # noqa: E402


class BudgetInputError(Exception):
    """任务根预算状态不可读（无 task.yaml / 校验失败 / 无有效预算）。"""


def load_effective_budget(task_root: str | Path) -> Dict:
    """读 task.yaml → fw-protocol validate_file().effective.budget（默认值已补全）。"""
    root = Path(task_root)
    task_yaml = root / "task.yaml"
    if not task_yaml.is_file():
        raise BudgetInputError(f"任务根找不到 task.yaml（应先用 fw-scaffold 生成 v2 目录树）：{task_yaml}")
    try:
        result = validate_file(task_yaml)
    except Exception as e:  # YAML/IO 异常 → 输入错误
        raise BudgetInputError(f"任务书解析失败（fw-protocol）: {type(e).__name__}: {e}") from e
    if not result.ok:
        issues = [i.message for i in result.errors][:10]
        raise BudgetInputError(
            f"任务书复校验失败（fw-protocol），预算拒绝读取：{len(result.errors)} 个 error\n"
            + "\n".join(f"  - {m}" for m in issues))
    return dict((result.effective or {}).get("budget") or {})


def build_budget_gate(task_root: str | Path,
                      meter: Optional[TokenMeter] = None) -> "BudgetGate":
    """用 effective.budget 构造真实 BudgetGate，并把历史累计消耗 record 进去。

    - warn_at/stop_at/per_module_max_tokens 默认值由 fw-protocol effective 补全
      （warn_at=0.7 / stop_at=1.0 / per_module_max_tokens 缺省= max_tokens）。
    - record 顺序不影响最终总量；resume 后 runner 会在每轮继续 record 新消耗，
      因此 check 的比例始终基于 **累计** 消耗（跨 resume 不失忆）。
    """
    b = load_effective_budget(task_root)
    gate = BudgetGate(
        max_tokens=int(b.get("max_tokens") or 1_000_000),
        warn_at=float(b.get("warn_at") if b.get("warn_at") is not None else 0.7),
        stop_at=float(b.get("stop_at") if b.get("stop_at") is not None else 1.0),
        per_module_max_tokens=b.get("per_module_max_tokens"),
    )
    if meter is not None:
        for mid, used in meter.per_module().items():
            gate.record(mid, used)
    return gate


def check_now(task_root: str | Path, meter: Optional[TokenMeter] = None) -> Dict:
    """当前预算闸门判定（不跑 runner）：返回 {status: BudgetStatus.to_dict(), ranking: [...]}。

    status 判定语义（与 fw-runner BudgetGate 一致）：
      stopped = used >= max_tokens*stop_at 或任一模块 >= per_module_max_tokens
      warned  = used >= max_tokens*warn_at（非 stop）
      ok      = 未达任何阈值
    """
    gate = build_budget_gate(task_root, meter=meter)
    st = gate.check()
    return {"budget": st.to_dict(), "ranking": gate.ranking()}


def trim_meter_to_events(meter: TokenMeter) -> Dict[str, int]:
    """meter.per_module() 的 dict 拷贝（供外部消费）。"""
    return dict(meter.per_module())
