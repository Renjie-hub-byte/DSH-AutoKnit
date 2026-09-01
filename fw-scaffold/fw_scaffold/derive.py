"""fw-scaffold 派生逻辑：任务目录名 / 日期 / 模块级任务书（原子合同）。

派生铁律（需求 2）：派生模块任务书与总任务书**语义一致、字段齐全、不自行扩展内容**。
实现方式 = 深拷贝 effective 任务书 → modules 只留本模块（其余字段原样保留：
task / budget / runtime / integration / 本模块的 dependencies/interfaces/acceptance/
boundaries 全量保留，不增删不改写）。上游/下游上下文只写进 YAML 注释头，不进入数据字段。

注意：派生书只含本模块，若其 dependencies 引用外部模块，fw-protocol 直接校验会报
dep_unknown_module —— 这是"子集任务书"的预期行为（语义一致性优先于单书可独立校验），
文档已如实标注；语义一致性由 scaffold 测试用逐字段比对保证。
"""
from __future__ import annotations

import copy
import datetime as _dt
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

import yaml


def sanitize_name(name: str) -> str:
    """目录安全化：保留 CJK/字母/数字/._-，其余转 '-'；合并连续 '-'；去首尾分隔符。"""
    if not name:
        return "task"
    s = re.sub(r"[^\w.\-]", "-", name, flags=re.UNICODE)
    s = re.sub(r"-+", "-", s)
    s = s.strip("-.")
    return s or "task"


def resolve_date(effective: Mapping[str, Any], today: _dt.date | None = None) -> str:
    """任务目录日期：取 task.created 的 YYYY-MM-DD（若为合法 ISO 日期），否则用今天。"""
    created = (effective.get("task") or {}).get("created")
    if isinstance(created, str):
        m = re.match(r"^(\d{4}-\d{2}-\d{2})", created)
        if m:
            return m.group(1)
    d = today or _dt.date.today()
    return d.isoformat()


def task_dir_name(effective: Mapping[str, Any], today: _dt.date | None = None) -> str:
    tname = sanitize_name(str((effective.get("task") or {}).get("name", "task")))
    return f"任务-{tname}_{resolve_date(effective, today)}"


def module_dir_name(module: Mapping[str, Any]) -> str:
    mid = str(module.get("id", ""))
    mname = sanitize_name(str(module.get("name", "module")))
    return f"{mid}-{mname}"


def _upstream_downstream(module: Mapping[str, Any],
                         all_modules: Sequence[Mapping[str, Any]]) -> Tuple[List[str], List[str]]:
    """上游（本模块依赖的 id）与下游（依赖本模块的 id），派生自总任务书依赖边。"""
    mid = module.get("id")
    upstream = [d for d in (module.get("dependencies") or []) if isinstance(d, str)]
    downstream = [m.get("id") for m in all_modules
                  if m.get("id") != mid and mid in set(m.get("dependencies") or [])]
    return sorted(upstream), sorted(downstream)


def _yaml_dump(obj: Any) -> str:
    return yaml.safe_dump(obj, allow_unicode=True, sort_keys=False, default_flow_style=False,
                          indent=2, width=120)


def derive_module_book(effective: Mapping[str, Any],
                       module: Mapping[str, Any],
                       all_modules: Sequence[Mapping[str, Any]]) -> str:
    """从总任务书派生模块级任务书 YAML（原子合同）。字段与总任务书逐字段一致。"""
    book = copy.deepcopy(effective)
    book["modules"] = [copy.deepcopy(module)]
    task_name = str((book.get("task") or {}).get("name", "?"))
    mid = str(module.get("id", "?"))
    mname = str(module.get("name", "?"))
    upstream, downstream = _upstream_downstream(module, all_modules)
    header = [
        f"# 派生模块任务书 —— {mid} {mname}（原子合同）",
        f"# 由 fw-scaffold 从总任务书《{task_name}》派生（需求 2）。执行/验收只依据本文件及其中引用：",
        "#   contracts/api.yaml（契约区）、REVIEW.md（验收闭环）、contract.yaml（接口契约）。",
        "# 上下文（派生自总任务书依赖边，非新内容）：",
        f"#   upstream（本模块输入来源）: {', '.join(upstream) if upstream else '（无）'}",
        f"#   downstream（依赖本模块的模块）: {', '.join(downstream) if downstream else '（无）'}",
        "# 说明：本文件是子集任务书，dependencies 保留总任务书原值（可能引用外部模块）；",
        "# 用 fw-protocol 直接校验时会报 dep_unknown_module，属预期（语义一致性优先）。",
    ]
    return "\n".join(header) + "\n" + _yaml_dump(book)


def effective_yaml(effective: Mapping[str, Any], task_name: str) -> str:
    """任务根 task.yaml 内容：effective 版本（默认值已补全）+ 说明头注释。"""
    header = [
        f"# task.yaml —— 任务书（effective 版本，默认值已补全）",
        f"# 由 fw-scaffold 从输入 task.yaml 校验通过后派生写入（fw-protocol validate_file().effective）。",
        f"# 任务：{task_name} ｜ 目录规范 v2 ｜ 下游（runner/integrate）以本文件为唯一事实源。",
    ]
    return "\n".join(header) + "\n" + _yaml_dump(effective)
