"""确定性 planner：只跑规划，不执行、不发 LLM 请求。

给定解析后的 PRD 结构，生成 :class:`TaskPlan`。所有估算都是同输入必同输出的
纯函数，保证可复现、可测试。
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import ExecutorBlock, Module, TaskPlan
from .prd_parser import ParsedPrd, Section

# 估算参数（启发式常量，集中定义便于调参）。
MIN_MODULE_LINES = 60        # 每个模块最少估算行数
LINES_FACTOR = 3.0           # 正文行 -> 估算行 的放大系数
MIN_FIRST_BLOCK_LINES = 10   # 每个模块首个 executor 块最少行数
FIRST_BLOCK_DIVISOR = 4      # 首个执行块约占总模块估行的比例分母
FIRST_BLOCK_SCOPE_LINES = 3  # 首个执行块 scope 摘要取正文前几行


@dataclass(frozen=True)
class EstimateResult:
    module_lines: int
    first_block_lines: int


def estimate_module_lines(body_lines: list[str]) -> int:
    """按正文非空行数推导模块估算行数（单调递增、确定性）。"""
    count = len(body_lines)
    return max(MIN_MODULE_LINES, round(count * LINES_FACTOR))


def estimate_first_block_lines(module_lines: int) -> int:
    """由模块估算行推导首个 executor 块估算行。"""
    return max(MIN_FIRST_BLOCK_LINES, round(module_lines / FIRST_BLOCK_DIVISOR))


def _first_block_scope(section: Section) -> str:
    lines = section.body_lines[:FIRST_BLOCK_SCOPE_LINES]
    if not lines:
        return f"{section.name}：首个执行块"
    return f"{section.name}：{'；'.join(lines)}"


def build_modules(prd: ParsedPrd) -> list[Module]:
    """把 PRD 段落转成模块列表（保持文档顺序）。"""
    modules: list[Module] = []
    for index, section in enumerate(prd.sections):
        if not section.name:
            continue
        module_lines = estimate_module_lines(section.body_lines)
        first_lines = estimate_first_block_lines(module_lines)
        block = ExecutorBlock(
            name=f"{section.name}/first",
            scope=_first_block_scope(section),
            estimate_lines=first_lines,
        )
        dependencies = [m.name for m in modules]  # 顺序依赖前序模块（简化、确定）
        modules.append(
            Module(
                name=section.name,
                scope=";".join(section.body_lines[:5]) or section.name,
                estimated_lines=module_lines,
                first_block=block,
                dependencies=dependencies,
            )
        )
    return modules


def plan(prd: ParsedPrd, execution_order: list[str] | None = None) -> TaskPlan:
    """由 PRD 生成完整规划（TaskPlan）。不触网、不写盘。"""
    modules = build_modules(prd)
    order = execution_order if execution_order is not None else [m.name for m in modules]
    return TaskPlan(
        task_name=prd.title,
        goal=prd.goal,
        execution_order=order,
        modules=modules,
    )
