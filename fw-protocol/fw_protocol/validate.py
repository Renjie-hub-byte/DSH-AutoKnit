"""fw-protocol 校验主入口：结构校验（JSON Schema）+ 三查语义校验。

流程：
0. 全角结构体检（仅 validate_file）——AI 产出 YAML 时误用全角字符：
   - 行首全角空格（U+3000）缩进 → 结构静默错位（字段变兄弟节点/值丢失），error
   - 结构性全角冒号（ASCII key 后接全角 ：）→ YAML 解析直接炸，error
   - 正文值里的全角标点（中文文本）→ 合法，不报
1. 结构校验 —— jsonschema（draft 2020-12）逐条转 Issue(code="schema*")
2. 默认值套用（深拷贝）→ effective（供 scaffold/runner/integrate 消费）
3. 语义三查（受 integration.check.* 开关控制）：
   - dependency_cycle     : 依赖环 DFS（含 id 唯一/未知依赖/重复依赖）
   - interface_duplicate  : 接口前缀+方法重复
   - acceptance_conflict  : "快 vs 安全"关键词冲突 → 需人工定优先级（不算 error）
4. 预算配置自检：warn_at<=stop_at；per_module_max_tokens<=max_tokens（warning）
5. 轮数预判自检：round_estimate>max_rounds_override 报 warning；>max_rounds_override×2 报 error（强制切开）

结构不合法时不跑语义三查（语义检查依赖结构成立），直接返回结构错误。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

import datetime as _dt
from jsonschema import Draft202012Validator

from .conflicts import validate_acceptance_conflicts
from .dependencies import validate_dependencies
from .interfaces import find_interface_duplicates, find_method_semantic_mismatch
from .io_utils import TaskYamlError, read_task_document
from .model import Issue, ValidationResult
from .schema import DEFAULT_VALUES, apply_defaults, load_schema


def _schema_issues(doc: Any, schema: Mapping[str, Any]) -> List[Issue]:
    """jsonschema 结构校验 → Issue 列表（含字段定位 path）。"""
    issues: List[Issue] = []
    validator = Draft202012Validator(schema)
    for err in validator.iter_errors(doc):
        path = "/".join(str(p) for p in err.absolute_path) if err.absolute_path else "$"
        issues.append(Issue(
            code="schema",
            severity="error",
            message=f"结构校验失败 [{path}]: {err.message}",
            detail={"path": path, "validator": err.validator, "message": err.message},
        ))
    return issues


# 全角结构体检 -----------------------------------------------------------
# 背景：planner(AI) 产出 task.yaml 时若误用全角字符，后果分两类——
#   - 全角空格缩进（U+3000 在行首缩进区）→ PyYAML 静默成功但结构错位：
#     `a:\n　　b: 1` 解析为 {'a': None, '　　b': 1}，字段变成兄弟节点、原 key 值丢失。
#     这正是"派生模块书缺 first_block → executor 静默全量执行"类 BUG 的输入层元凶。
#   - 结构性全角冒号（ASCII key 后接 ：）→ ScannerError 直接炸，但报错信息对 AI 不友好。
# 正文值里的全角标点（中文描述文本）完全合法，不报。

_FULLWIDTH_INDENT = "\u3000"  # 全角空格


def _fullwidth_issues(raw: str) -> List[Issue]:
    """扫描 task.yaml 原始文本的结构性全角字符，返回 Issue 列表。"""
    issues: List[Issue] = []
    for lineno, line in enumerate(raw.splitlines(), 1):
        # 1) 行首缩进区含全角空格 → 结构静默错位（error）
        # 注意：U+3000 不能被 lstrip(" \t") 去掉，必须单独算行首空白区
        lead_end = 0
        while lead_end < len(line) and line[lead_end] in (" ", "\t", "\u3000"):
            lead_end += 1
        lead = line[:lead_end]
        stripped = line[lead_end:]
        if _FULLWIDTH_INDENT in lead:
            issues.append(Issue(
                code="fullwidth",
                severity="error",
                message=(
                    f"第 {lineno} 行：缩进区含全角空格（U+3000）。"
                    "YAML 会静默解析成功但结构错位——该行字段将变成兄弟节点、原 key 值丢失。"
                    "请改用 ASCII 空格缩进。"
                ),
                detail={"line": lineno, "kind": "fullwidth_indent"},
            ))
            continue
        # 2) ASCII key 后接全角冒号（结构性分隔符）→ 解析必炸（error）
        key_head = stripped[:1]
        if key_head and key_head not in "#-|>%&*!@`'\"" and "：" in stripped:
            # key 位置：行首（可含 - 列表符）到全角冒号之间只能有 ASCII key 字符
            idx = stripped.find("：")
            prefix = stripped[:idx]
            if prefix and all(c.isascii() and (c.isalnum() or c in "._-/ ") for c in prefix):
                issues.append(Issue(
                    code="fullwidth",
                    severity="error",
                    message=(
                        f"第 {lineno} 行：key 分隔符用了全角冒号（：）。"
                        "YAML 分隔符必须是 ASCII 冒号（:），全角冒号会导致解析失败。"
                        f"疑似 key：{prefix.strip()[:40]}"
                    ),
                    detail={"line": lineno, "kind": "fullwidth_colon", "key": prefix.strip()},
                ))
    return issues


def _budget_issues(doc: Mapping[str, Any]) -> List[Issue]:
    """预算配置自检：warn_at<=stop_at（error）；per_module_max_tokens<=max_tokens（warning）。"""
    issues: List[Issue] = []
    budget = doc.get("budget")
    if not isinstance(budget, dict):
        return issues
    warn_at = budget.get("warn_at")
    stop_at = budget.get("stop_at")
    if isinstance(warn_at, (int, float)) and isinstance(stop_at, (int, float)) and warn_at > stop_at:
        issues.append(Issue(
            code="budget_range_invalid",
            severity="error",
            message=f"预算配置矛盾：warn_at({warn_at}) > stop_at({stop_at})，预警会晚于硬停",
            detail={"warn_at": warn_at, "stop_at": stop_at},
        ))
    max_tokens = budget.get("max_tokens")
    per_module = budget.get("per_module_max_tokens")
    if (isinstance(max_tokens, int) and isinstance(per_module, int)
            and per_module > max_tokens):
        issues.append(Issue(
            code="budget_per_module_gt_global",
            severity="warning",
            message=f"per_module_max_tokens({per_module}) > max_tokens({max_tokens})，单模块上限失去约束意义",
            detail={"per_module_max_tokens": per_module, "max_tokens": max_tokens},
        ))
    return issues


def _round_estimate_issues(doc: Mapping[str, Any]) -> List[Issue]:
    """轮数预判自检：规划期预判模块大小，避免 executor 运行时撞轮数上限。

    规则（仅当模块显式填了 round_estimate 时检查；未填 = 无预估，不告警）：
    - round_estimate < 1    → 结构层被 schema minimum:1 拦下（此处不重复报）
    - round_estimate > 上限  → warning（模块可能过大，建议切开）
    - round_estimate > 上限×2 → error（强制切开）
    上限 = 模块 max_rounds_override（effective 中已默认继承 runtime.executor_max_rounds）。
    """
    issues: List[Issue] = []
    modules = doc.get("modules")
    if not isinstance(modules, list):
        return issues
    runtime = doc.get("runtime")
    default_cap = runtime.get("executor_max_rounds") if isinstance(runtime, dict) else None
    if not isinstance(default_cap, int) or isinstance(default_cap, bool):
        default_cap = DEFAULT_VALUES["runtime"]["executor_max_rounds"]
    for m in modules:
        if not isinstance(m, dict):
            continue
        est = m.get("round_estimate")
        if not isinstance(est, int) or isinstance(est, bool):
            continue  # 未填或类型非法（类型非法已由 schema 拦下，结构不合法时不走到这里）
        cap = m.get("max_rounds_override")
        if not isinstance(cap, int) or isinstance(cap, bool):
            cap = default_cap
        mid = m.get("id", "?")
        if est > cap * 2:
            issues.append(Issue(
                code="module_round_estimate_too_large",
                severity="error",
                module_id=mid,
                message=f"模块 {mid} 预估 {est} 轮 > 上限 {cap}×2，规划期必须切开成更小的子模块"
                        f"（横向并行 A1/A2 或纵向串行 A1→A2），不得让 executor 撞轮数上限",
                detail={"round_estimate": est, "max_rounds": cap},
            ))
        elif est > cap:
            issues.append(Issue(
                code="module_round_estimate_over_cap",
                severity="warning",
                module_id=mid,
                message=f"模块 {mid} 预估 {est} 轮超过上限 {cap}，建议规划期切开减小模块",
                detail={"round_estimate": est, "max_rounds": cap},
            ))
    return issues


def _semantic_issues(doc: Mapping[str, Any], check: Mapping[str, Any],
                     groups: Optional[Mapping[str, Sequence[str]]] = None) -> List[Issue]:
    issues: List[Issue] = []
    modules: List[Dict[str, Any]] = [m for m in doc.get("modules") if isinstance(m, dict)] \
        if isinstance(doc.get("modules"), list) else []
    if not modules:
        return issues
    if check.get("dependency_cycle", True):
        issues.extend(validate_dependencies(modules))
    if check.get("interface_duplicate", True):
        issues.extend(find_interface_duplicates(modules))
    if check.get("method_semantic", True):
        issues.extend(find_method_semantic_mismatch(modules))
    if check.get("acceptance_conflict", True):
        issues.extend(validate_acceptance_conflicts(modules, groups))
    return issues



def _normalize_dates(obj: Any) -> Any:
    """YAML 隐式把 ISO 日期解析成 date/datetime 对象；协议要求 string，这里统一转 ISO 字符串。"""
    if isinstance(obj, (_dt.date, _dt.datetime)):
        return obj.isoformat()
    if isinstance(obj, list):
        return [_normalize_dates(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _normalize_dates(v) for k, v in obj.items()}
    return obj


def validate_document(doc: Any,
                      schema: Optional[Mapping[str, Any]] = None,
                      groups: Optional[Mapping[str, Sequence[str]]] = None,
                      ) -> ValidationResult:
    """校验一份任务书（dict）。schema 缺省用包内置；groups 可覆盖冲突关键词组。

    - 结构不合法：返回 errors（含所有结构错误）+ effective（尽力套默认值），不跑语义三查。
    - 结构合法：跑语义三查 + 预算自检；conflict 与 error 分离。
    """
    doc = _normalize_dates(doc)
    sch = schema if schema is not None else load_schema()
    if not isinstance(doc, dict):
        return ValidationResult(
            errors=(Issue(code="schema", severity="error",
                          message="任务书根节点必须是对象（YAML 映射）",
                          detail={"path": "$"}),),
            effective={},
        )

    effective = apply_defaults(doc)
    structure_issues = _schema_issues(doc, sch)

    if structure_issues:
        return ValidationResult(errors=tuple(structure_issues), effective=effective)

    integ = effective.get("integration")
    check_cfg = integ.get("check", {}) if isinstance(integ, dict) else {}
    semantic = _semantic_issues(effective, check_cfg, groups)
    budget = _budget_issues(effective)
    rounds = _round_estimate_issues(effective)

    errors: List[Issue] = [i for i in semantic + budget + rounds if i.severity == "error"]
    conflicts: List[Issue] = [i for i in semantic + budget + rounds if i.severity == "conflict"]
    warnings: List[Issue] = [i for i in semantic + budget + rounds if i.severity == "warning"]
    return ValidationResult(
        errors=tuple(errors),
        conflicts=tuple(conflicts),
        warnings=tuple(warnings),
        effective=effective,
    )


def validate_file(path: str | Path,
                  schema: Optional[Mapping[str, Any]] = None,
                  groups: Optional[Mapping[str, Sequence[str]]] = None) -> ValidationResult:
    """直接校验一个 task.yaml 文件（自动 YAML 解析）。

    在解析前先跑全角结构体检（_fullwidth_issues）：全角空格缩进/全角冒号分隔符
    在 YAML 层会静默错位或解析失败，必须显式报出来。解析失败时也把全角体检结果
    一并返回，方便 AI 定位到具体行。
    """
    p = Path(path)
    if not p.exists():
        raise TaskYamlError(f"文件不存在: {p}")
    raw = p.read_text(encoding="utf-8")
    fw_issues = _fullwidth_issues(raw)
    fw_errors = [i for i in fw_issues if i.severity == "error"]
    fw_warnings = [i for i in fw_issues if i.severity == "warning"]

    try:
        doc = read_task_document(p)
    except TaskYamlError as e:
        # 解析失败：全角体检结果 + 解析错误合并为结构化 Issue
        issues = list(fw_errors)
        if not issues:
            issues.append(Issue(
                code="yaml_parse", severity="error",
                message=str(e), detail={"path": str(p)},
            ))
        return ValidationResult(
            errors=tuple(issues), conflicts=(), warnings=tuple(fw_warnings), effective={},
        )

    result = validate_document(doc, schema=schema, groups=groups)
    if not fw_issues:
        return result
    return ValidationResult(
        errors=tuple(list(result.errors) + fw_errors),
        conflicts=result.conflicts,
        warnings=tuple(list(result.warnings) + fw_warnings),
        effective=result.effective,
    )
