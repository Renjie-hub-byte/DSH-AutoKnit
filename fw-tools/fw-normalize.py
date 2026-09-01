#!/usr/bin/env python3
"""fw-normalize — planner 原始产物 → 标准 task.yaml 结构化适配层。

原则：AI 只产内容，程序接管结构。
planner 只输出"纯内容"（任务名/目标/模块名/目标/验收/依赖名/分块目标），
结构性字段全部由本工具补全（id/layer/created/grade/budget/runtime）。
同时容忍 AI 常见脏输出（全角标点、模块 dict 形态、分块字段放错位置、尾逗号）。

流程：
1. 容错解析：严格 JSON → 全角标点规范化 → 尾逗号/单引号修复 → YAML 兜底
2. 字段归位（分门别类放对位置）：
   - modules 若是 dict → 转 list（id 取 value.id 或 key）
   - 分块三字段 first_block/remaining_estimate/max_rounds_override 若误放顶层
     → 逐模块注入（BUG-001 教训：必须模块级）
   - 其他散落顶层字段按 schema 白名单归类到 task/budget/runtime
   - 未知顶层字段 → error（不静默丢数据）
3. 结构补全（content-mode，降低 AI 执行难度）：
   - task：name/created/grade/owner/source_prd 缺省注入（meta 来自 CLI）
   - budget/runtime：缺省注入默认值（AI 写了部分则合并，不覆盖）
   - modules：必填校验（name/objective/acceptance）；id 缺省 m01..；id 唯一性；
     dependencies 支持"模块名引用"→ 自动解析成 id；layer 按依赖拓扑推导
     （无依赖=1，依赖最大层+1）；缺省字段补默认（dependencies/boundaries/
     environment/interfaces/round_estimate）
4. 标准化输出：yaml.dump（半角缩进、固定顺序、unicode 保留）

用法：
    fw-normalize.py <planner-raw.json> -o <task.yaml> \
        [--name 任务名] [--owner 负责人] [--source-prd PRD文件名] [--created 日期]

退出码：0=成功；2=容错修复后成功（warnings 非空）；1=失败（无法修复）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# 1. 容错解析
# ---------------------------------------------------------------------------

# 全角 → 半角（语法字符映射；中文值里的标点转半角语义无伤，容错路径可接受）
_FULLWIDTH_MAP = str.maketrans({
    "：": ":", "，": ",", "；": ";", "！": "!", "？": "?",
    "（": "(", "）": ")", "【": "[", "】": "]", "｛": "{", "｝": "}",
    "“": '"', "”": '"', "‘": "'", "’": "'", "～": "~", "＝": "=",
    "／": "/", "．": ".", "％": "%", "＆": "&", "＠": "@", "＃": "#",
    "＄": "$", "＋": "+", "－": "-", "＊": "*", "｜": "|", "\u3000": " ",
})


def _try_json(text: str) -> dict | None:
    try:
        doc = json.loads(text)
        return doc if isinstance(doc, dict) else None
    except Exception:
        return None


def _strip_tail_commas(text: str) -> str:
    """去尾逗号 + 单引号转双引号（json5 常见脏输出）。"""
    # 去掉 , 后面紧跟 ] 或 }（含中间空白）的尾逗号
    text = re.sub(r",\s*([\]}])", r"\1", text)
    # 单引号字符串 → 双引号（仅当内部无双引号冲突时，粗略处理）
    # 不做完全 json5；只处理简单形态
    return text


def _parse_loose(raw: str) -> tuple[dict, list[str]]:
    """容错解析：返回 (doc, warnings)。失败抛 ValueError（带行号定位）。"""
    warnings: list[str] = []

    doc = _try_json(raw)
    if doc is not None:
        return doc, warnings

    # 2) 全角标点规范化后重试
    norm = raw.translate(_FULLWIDTH_MAP)
    if norm != raw:
        warnings.append("检测到全角标点，已规范化为半角后解析")
        doc = _try_json(norm)
        if doc is not None:
            return doc, warnings

    # 3) 尾逗号/单引号修复后重试
    fixed = _strip_tail_commas(norm if norm != raw else raw)
    if fixed != raw:
        doc = _try_json(fixed)
        if doc is not None:
            warnings.append("检测到尾逗号/单引号，已自动修复")
            return doc, warnings

    # 4) YAML 兜底（AI 可能无视指令仍输出 YAML）
    try:
        doc = yaml.safe_load(raw)
        if isinstance(doc, dict):
            warnings.append("planner 输出为 YAML（非指令要求 JSON），已按 YAML 解析")
            return doc, warnings
    except Exception:
        pass

    # 5) 全部失败：带行号报错
    lines = raw.splitlines()
    for i, ln in enumerate(lines[:5], 1):
        if any(c in ln for c in "：，（）【】"):
            raise ValueError(
                f"第 {i} 行疑似全角标点且规范化后仍无法解析：{ln[:60]!r}"
            )
    raise ValueError("产物既不是合法 JSON 也不是合法 YAML（前 5 行见上），请检查 planner 输出")


# ---------------------------------------------------------------------------
# 2. 字段归位
# ---------------------------------------------------------------------------

_TOP_LEVEL = {"task", "budget", "runtime", "modules", "integration"}

# 散落顶层字段 → 归属 section（白名单来自 task-schema.json）
_SECTION_KEYS: dict[str, list[str]] = {
    "task": ["name", "source_prd", "owner", "created", "grade", "goal",
             "execution_order", "prediction_baseline", "data_contract"],
    "budget": ["max_tokens", "warn_at", "stop_at", "per_module_max_tokens"],
    "runtime": ["models", "max_parallel", "executor_max_rounds",
                "retry_before_switch", "max_executor_switches", "end_gate"],
}
# 分块三字段必须模块级（BUG-001 教训）
_MODULE_LEVEL = {"first_block", "remaining_estimate", "max_rounds_override"}

# 模块级字段白名单（归一化时只保留这些 key，防止 AI 塞 extra）
_MODULE_KEYS = {"id", "name", "layer", "objective", "dependencies", "interfaces",
                "acceptance", "boundaries", "round_estimate", "max_rounds_override",
                "environment", "first_block", "remaining_estimate"}


def _normalize_modules(modules: object, warnings: list[str]) -> list[dict]:
    if modules is None:
        return []
    if isinstance(modules, dict):
        warnings.append("modules 为 dict 形态，已按 key 转 list（id 取 value.id 或 key，name 缺省用 key）")
        out: list[dict] = []
        for idx, (key, m) in enumerate(modules.items(), 1):
            if not isinstance(m, dict):
                m = {"name": str(m)}
            m = dict(m)
            m.setdefault("id", key if re.fullmatch(r"m\d+", str(key)) else f"m{idx:02d}")
            m.setdefault("name", str(key))
            out.append(m)
        return out
    if isinstance(modules, list):
        out = []
        for idx, m in enumerate(modules, 1):
            if not isinstance(m, dict):
                m = {"name": str(m)}
            m = dict(m)
            m.setdefault("id", f"m{idx:02d}")
            out.append(m)
        return out
    raise ValueError(f"modules 必须是 list 或 dict，实际是 {type(modules).__name__}")


def _relocate_loose_keys(doc: dict, warnings: list[str]) -> None:
    """把顶层散落字段按白名单归位；分块三字段注入每个模块。"""
    modules = doc.get("modules") or []
    if not isinstance(modules, list):
        return

    leftovers = {k: v for k, v in doc.items() if k not in _TOP_LEVEL}
    for key, val in leftovers.items():
        if key in _MODULE_LEVEL:
            # 分块字段：注入每个模块（BUG-001 根因兜底）
            warnings.append(f"分块字段 '{key}' 误放 task 顶层，已自动注入每个模块（必须模块级）")
            for m in modules:
                m.setdefault(key, val)
            doc.pop(key)
        elif any(key in ks for ks in _SECTION_KEYS.values()):
            sec = next(s for s, ks in _SECTION_KEYS.items() if key in ks)
            doc.setdefault(sec, {})[key] = val
            warnings.append(f"字段 '{key}' 散落顶层，已归入 {sec} 下")
            doc.pop(key)
        else:
            # 未知字段不静默丢，报给上层
            raise ValueError(f"未知顶层字段 '{key}'（schema 无此定义），请修正 planner 输出")


def _ensure_task_name(doc: dict, fallback_name: str | None) -> None:
    if fallback_name and not (doc.get("task") or {}).get("name"):
        doc.setdefault("task", {})["name"] = fallback_name


# ---------------------------------------------------------------------------
# 2.5 结构补全（content-mode：AI 只填内容，程序补全结构性字段）
# ---------------------------------------------------------------------------

# budget/runtime 默认值（planner 可省略；AI 写了部分则按 key 合并）
_DEFAULT_BUDGET: dict = {
    "max_tokens": 200000,
    "warn_at": 0.7,
    "stop_at": 1.0,
    "per_module_max_tokens": 60000,
}
_DEFAULT_RUNTIME: dict = {
    "models": {"planner": "deepseek-v4-flash",
               "executor": "deepseek-v4-flash",
               "auditor": "deepseek-v4-flash"},
    "max_parallel": 2,
    "executor_max_rounds": 5,
    "retry_before_switch": 2,
    "max_executor_switches": 1,
    "end_gate": "auto",
}
# 模块可缺省字段 → 默认值（结构性的程序补，内容性的 AI 给）
_MODULE_DEFAULTS: dict = {
    "dependencies": [],
    "interfaces": [],
    "boundaries": [],
    "environment": {"python_packages": [], "system_tools": []},
    "round_estimate": 2,
}
# 模块必填内容字段（程序不能编的智力活）
_MODULE_REQUIRED = ("name", "objective", "acceptance")


def _merge_section_defaults(doc: dict, section: str, defaults: dict,
                            warnings: list[str]) -> None:
    """budget/runtime 补默认：整块缺失→全默认；写了一半→按 key 合并不覆盖。"""
    cur = doc.get(section)
    if cur is None:
        doc[section] = dict(defaults)
    elif isinstance(cur, dict):
        missing = [k for k in defaults if k not in cur]
        if missing:
            warnings.append(f"{section} 缺少字段 {missing}，已用默认值补齐")
            for k in missing:
                cur[k] = defaults[k]
    else:
        raise ValueError(f"{section} 必须是对象，实际是 {type(cur).__name__}")


def _resolve_dependency_names(mods: list[dict]) -> None:
    """dependencies 支持模块名引用 → 解析成 id；未知引用/环 → 报错。"""
    name2id = {m.get("name"): m["id"] for m in mods if m.get("name")}
    id_set = {m["id"] for m in mods}
    for m in mods:
        deps = m.get("dependencies") or []
        if not isinstance(deps, list):
            raise ValueError(f"模块 {m['id']} 的 dependencies 必须是数组，实际是 {type(deps).__name__}")
        resolved: list[str] = []
        for d in deps:
            if isinstance(d, str) and d in name2id:
                resolved.append(name2id[d])
            elif isinstance(d, str) and d in id_set:
                resolved.append(d)
            else:
                known = list(name2id)
                raise ValueError(
                    f"模块 {m['id']} 依赖 '{d}' 不存在——可用模块名: {known or '（无）'}"
                )
        m["dependencies"] = resolved


def _derive_layers(mods: list[dict]) -> None:
    """layer 拓扑推导：无依赖=1；依赖最大层+1；检测环。"""
    layer: dict[str, int] = {}

    def compute(mid: str, seen: list[str]) -> int:
        if mid in layer:
            return layer[mid]
        if mid in seen:
            raise ValueError(f"依赖环: {' → '.join(seen + [mid])}")
        m = next(x for x in mods if x["id"] == mid)
        deps = m.get("dependencies") or []
        if not deps:
            layer[mid] = 1
        else:
            layer[mid] = max(compute(d, seen + [mid]) for d in deps) + 1
        return layer[mid]

    for m in mods:
        compute(m["id"], [])
    for m in mods:
        m["layer"] = layer[m["id"]]


# first_block/remaining_estimate 内部字段纠正（BUG-001/content-mode 教训：planner 常写错 name/lines/rounds）
_BLOCK_ALIASES: dict[str, dict[str, str]] = {
    "first_block": {"name": "scope", "lines": "estimate_lines"},
    "remaining_estimate": {"lines": "estimate_lines"},
}
_BLOCK_LEGAL: dict[str, set[str]] = {
    "first_block": {"scope", "estimate_lines", "acceptance"},
    "remaining_estimate": {"scope", "estimate_lines"},
}


def _normalize_block_fields(mods: list[dict], warnings: list[str]) -> None:
    """纠正 first_block/remaining_estimate 内部字段名（name→scope、lines→estimate_lines）；
    丢弃 schema 未定义字段（如 remaining_estimate.rounds）；缺 scope 告警。"""
    for m in mods:
        for key, aliases in _BLOCK_ALIASES.items():
            blk = m.get(key)
            if not isinstance(blk, dict):
                continue
            # 字段名纠正（写错了程序兜）
            for wrong, right in aliases.items():
                if wrong in blk and right not in blk:
                    blk[right] = blk.pop(wrong)
                    warnings.append(f"模块 {m['id']} {key}.{wrong} 已纠正为 {right}")
            # 丢弃 schema 未定义字段（不静默保留，避免 validate 时脏字段）
            for k in [x for x in blk if x not in _BLOCK_LEGAL[key]]:
                blk.pop(k)
                warnings.append(f"模块 {m['id']} {key}.{k} 是 schema 未定义字段，已丢弃")
            # 缺 scope 告警（首发块/剩余没写"做什么"，executor/split 读不到分块目标）
            if not str(blk.get("scope", "")).strip():
                warnings.append(f"模块 {m['id']} {key} 缺 scope（做什么），分块目标可能失效")


def _complete(doc: dict, fallback_name: str | None, owner: str | None,
              source_prd: str | None, created: str | None) -> list[str]:
    """结构补全：meta 注入 + 必填校验 + id/layer 推导 + 缺省字段。返回 warnings。"""
    warnings: list[str] = []
    import datetime

    # --- meta：task / budget / runtime ---
    task = doc.setdefault("task", {})
    task.setdefault("name", fallback_name or "未命名任务")
    task.setdefault("created", created or datetime.date.today().isoformat())
    task.setdefault("grade", "B")
    if owner:
        task.setdefault("owner", owner)
    if source_prd:
        task.setdefault("source_prd", source_prd)
    _merge_section_defaults(doc, "budget", _DEFAULT_BUDGET, warnings)
    _merge_section_defaults(doc, "runtime", _DEFAULT_RUNTIME, warnings)

    # --- modules ---
    mods = doc.get("modules") or []
    if not mods:
        raise ValueError("modules 为空——planner 至少要产出 1 个模块")

    # 必填内容校验（缺 acceptance 程序不能编）
    for m in mods:
        for field in _MODULE_REQUIRED:
            val = m.get(field)
            if field == "acceptance":
                if not isinstance(val, list) or not val:
                    raise ValueError(
                        f"模块 {m.get('id') or m.get('name') or '?'} 缺少 acceptance"
                        f"（至少 1 条可测验收，程序无法代填）")
            elif not val:
                raise ValueError(
                    f"模块 {m.get('id') or m.get('name') or '?'} 缺少 {field}（程序无法代填）")

    # id 缺省 + 唯一性
    for idx, m in enumerate(mods, 1):
        m.setdefault("id", f"m{idx:02d}")
    ids = [m["id"] for m in mods]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        raise ValueError(f"模块 id 重复: {sorted(dup)}")

    # 缺省字段（不覆盖 AI 已写的）
    for m in mods:
        for k, v in _MODULE_DEFAULTS.items():
            m.setdefault(k, v)

    # 分块子结构字段纠正（first_block/remaining_estimate 的 name→scope、lines→estimate_lines）
    _normalize_block_fields(mods, warnings)

    # 依赖名解析 → layer 拓扑推导
    _resolve_dependency_names(mods)
    _derive_layers(mods)
    return warnings


def normalize(doc: dict, fallback_name: str | None = None,
              owner: str | None = None, source_prd: str | None = None,
              created: str | None = None) -> tuple[dict, list[str]]:
    """字段归位 + 结构补全。返回 (归一化 doc, warnings)。"""
    warnings: list[str] = []
    _ensure_task_name(doc, fallback_name)

    mods = _normalize_modules(doc.get("modules"), warnings)
    # 模块 key 白名单裁剪（防 AI 塞 extra 字段被 schema 拒）
    mods = [{k: m[k] for k in _MODULE_KEYS if k in m} for m in mods]
    doc["modules"] = mods

    _relocate_loose_keys(doc, warnings)
    warnings += _complete(doc, fallback_name, owner, source_prd, created)
    return doc, warnings


# ---------------------------------------------------------------------------
# 3. 主流程
# ---------------------------------------------------------------------------

class _NoAliasDumper(yaml.SafeDumper):
    """禁止 YAML 锚点复用（&id001）：相同空对象若被下游共享，一处 append 会互相污染。"""

    def ignore_aliases(self, data) -> bool:
        return True


def main() -> int:
    ap = argparse.ArgumentParser(description="planner 原始产物 → 标准 task.yaml")
    ap.add_argument("input", help="planner 输出文件（JSON，兼容 YAML）")
    ap.add_argument("-o", "--output", required=True, help="输出 task.yaml 路径")
    ap.add_argument("--name", default=None, help="任务名兜底（task.name 缺失时注入）")
    ap.add_argument("--owner", default=None, help="负责人（task.owner 缺失时注入）")
    ap.add_argument("--source-prd", default=None, help="PRD 文件名（task.source_prd 缺失时注入）")
    ap.add_argument("--created", default=None, help="创建日期 YYYY-MM-DD（缺省今天）")
    args = ap.parse_args()

    raw = Path(args.input).read_text(encoding="utf-8")
    try:
        doc, parse_warnings = _parse_loose(raw)
    except ValueError as e:
        print(f"✗ fw-normalize 解析失败: {e}", file=sys.stderr)
        return 1

    try:
        doc, norm_warnings = normalize(
            doc, fallback_name=args.name, owner=args.owner,
            source_prd=args.source_prd, created=args.created)
    except ValueError as e:
        print(f"✗ fw-normalize 归位失败: {e}", file=sys.stderr)
        return 1

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        yaml.dump(doc, f, Dumper=_NoAliasDumper, allow_unicode=True,
                  sort_keys=False, default_flow_style=False, width=1000)

    all_warnings = parse_warnings + norm_warnings
    for w in all_warnings:
        print(f"  ⚠ fw-normalize: {w}")
    print(f"✓ task.yaml 已生成（{out}，{len(doc.get('modules') or [])} 个模块）")
    return 2 if all_warnings else 0


if __name__ == "__main__":
    sys.exit(main())
