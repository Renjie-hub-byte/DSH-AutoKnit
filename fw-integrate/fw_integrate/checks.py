"""fw-integrate 程序检查项（需求6）：接口匹配 / 数据格式 / 跨模块数据依赖。

三大检查全部是**运行时契约校验**（不只人工比对预测基线）：
1. check_interfaces        —— 接口匹配：契约区 contracts/api.yaml vs 各模块 contract.yaml
   read_api；两模块同接口前缀+方法 → 报错并指出**具体哪两个模块**（验收1）。
2. check_data_format       —— 数据格式：各模块 output.artifacts 声明产物必须存在，且按扩展名
   做解析级格式校验（json/yaml/csv）；解析失败 → 报错（运行时不只存在性）。
3. check_data_dependency   —— 跨模块数据依赖：B 的 input.from 需要 A 的输入时，A 的 output
   必须声明过产物（B 需要的输入是否 A 的 output 声明过）；shared/ 文件存在性检查。

诚实边界：格式校验是“解析级”（JSON/YAML/CSV 可解析）；字段级 schema 校验不在契约里
（规划期禁止硬定字段，output.describe 是自然语言）——见 docs/integrate-spec.md 已知限制。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .context import IntegrateContext

_MID_RE = re.compile(r"^m\d+$")
_ANY_MID_IN_PATH = re.compile(r"(?:^|/)(m\d+)/")


# ---------------------------------------------------------------- findings

@dataclass
class Finding:
    """一条检查发现。severity: error | warning | info。"""

    kind: str            # 检查内部种类（见各检查函数）
    severity: str
    module: str = ""
    module_b: str = ""   # 涉及的第二模块（跨模块冲突/依赖时）
    path: str = ""
    method: str = ""
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"kind": self.kind, "severity": self.severity, "message": self.message}
        if self.module:
            d["module"] = self.module
        if self.module_b:
            d["module_b"] = self.module_b
        if self.path:
            d["path"] = self.path
        if self.method:
            d["method"] = self.method
        return d


@dataclass
class CheckResult:
    """一类检查的完整结果（机器可解析）。"""

    name: str
    ok: bool
    findings: List[Finding] = field(default_factory=list)

    @property
    def errors(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == "warning"]

    @property
    def infos(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == "info"]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name, "ok": self.ok,
            "counts": {"error": len(self.errors), "warning": len(self.warnings),
                       "info": len(self.infos)},
            "errors": [f.to_dict() for f in self.errors],
            "warnings": [f.to_dict() for f in self.warnings],
            "infos": [f.to_dict() for f in self.infos],
        }


def _add(result: CheckResult, **kw: Any) -> None:
    result.findings.append(Finding(**kw))


# ---------------------------------------------------------------- 接口匹配

def check_interfaces(ic: IntegrateContext) -> CheckResult:
    """接口匹配：契约区 vs 模块 read_api；跨模块重复/不匹配 → 报错指出哪两个模块。

    语义（与 fw-protocol 接口重复检测同源，这里做**运行时**侧）：
    - 契约区登记给模块 X 的 (path, method)，X/contract.yaml read_api 必须声明
      （缺 → contract_vs_module_missing，error）。
    - X 的 read_api 声明了 (path, method)，但契约区登记给 **另一个模块 Y**（或 Y 的 read_api
      也有同款）→ cross_module_duplicate，error，消息同时带 X 与 Y（验收1 的核心形态）。
    - X 的 read_api 声明了契约区未登记、其它模块也没有的接口 → unregistered，warning。
    """
    result = CheckResult(name="interface", ok=True)
    area_by_module: Dict[str, set] = {}
    for e in ic.contract_area:
        area_by_module.setdefault(e["module"], set()).add((e["path"], e["method"]))
    read_by_module: Dict[str, set] = {mid: ic.module_contracts[mid].read_api_pairs
                                      for mid in ic.module_order}
    all_modules = sorted(set(ic.module_order) | set(area_by_module))

    for mid in all_modules:
        expected = area_by_module.get(mid, set())
        actual = read_by_module.get(mid, set())
        for pm in sorted(expected - actual):
            _add(result, kind="contract_vs_module_missing", severity="error", module=mid,
                 path=pm[0], method=pm[1],
                 message=(f"{mid}: 契约区 contracts/api.yaml 登记 {pm[1]} {pm[0]}，"
                          f"但 {mid}/contract.yaml read_api 未声明（模块运行时丢失接口声明）"))
        for pm in sorted(actual - expected):
            other = _conflicting_module(all_modules, mid, pm, area_by_module, read_by_module)
            if other is not None:
                _add(result, kind="cross_module_duplicate", severity="error",
                     module=mid, module_b=other, path=pm[0], method=pm[1],
                     message=(f"{mid} 与 {other} 接口不匹配：{pm[1]} {pm[0]}"
                              f"（{mid}/contract.yaml read_api 声明了登记给 {other} 的接口）"))
            else:
                _add(result, kind="unregistered", severity="warning", module=mid,
                     path=pm[0], method=pm[1],
                     message=(f"{mid}: read_api 声明 {pm[1]} {pm[0]} 未在 contracts/api.yaml 登记"
                              f"（未登记接口，契约区应同步）"))

    # 模块间 read_api 直接碰撞（无论契约区；fw-protocol 的接口重复检测在规划期也会拦，
    # 这里是运行时侧双保险）
    ids = sorted(read_by_module)
    seen: set = set()
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            overlap = (read_by_module[a] & read_by_module[b])
            for pm in sorted(overlap):
                key = (a, b, pm)
                if key in seen:
                    continue
                seen.add(key)
                _add(result, kind="cross_module_duplicate", severity="error",
                     module=a, module_b=b, path=pm[0], method=pm[1],
                     message=(f"{a} 与 {b} 接口重复：两者 read_api 都声明了 {pm[1]} {pm[0]}"))

    result.ok = not result.errors
    return result


def _conflicting_module(all_modules: List[str], mid: str, pm: Tuple[str, str],
                        area_by_module: Dict[str, set],
                        read_by_module: Dict[str, set]) -> Optional[str]:
    """找与 (mid, pm) 冲突的其它模块：契约区登记给它的 或 它的 read_api 声明了 pm。"""
    for mid2 in all_modules:
        if mid2 == mid:
            continue
        if pm in area_by_module.get(mid2, set()) or pm in read_by_module.get(mid2, set()):
            return mid2
    return None


# ---------------------------------------------------------------- 数据格式

_JSON_SUFFIXES = (".json",)
_YAML_SUFFIXES = (".yaml", ".yml")
_CSV_SUFFIXES = (".csv",)
_TEXT_SUFFIXES = (".txt", ".md")


def parse_artifact(path: str | Path) -> Tuple[Optional[bool], Optional[str]]:
    """按扩展名做解析级格式校验。

    返回 (ok, err)：ok=None 表示该扩展名不做解析（仅存在性，诚实标注）；
    ok=True 解析通过；ok=False 解析失败（err 为原因）。
    """
    p = Path(path)
    suffix = p.suffix.lower()
    try:
        data = p.read_bytes()
    except OSError as e:
        return False, f"读取失败: {e}"
    if suffix in _JSON_SUFFIXES:
        try:
            json.loads(data.decode("utf-8"))
            return True, None
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            return False, f"JSON 解析失败: {e}"
    if suffix in _YAML_SUFFIXES:
        try:
            import yaml
            yaml.safe_load(data.decode("utf-8"))
            return True, None
        except Exception as e:  # noqa: BLE001 —— YAML 解析器异常种类多，统一按失败
            return False, f"YAML 解析失败: {e}"
    if suffix in _CSV_SUFFIXES:
        try:
            import csv as _csv
            import io
            rows = list(_csv.reader(io.StringIO(data.decode("utf-8-sig"))))
            if len(rows) == 0:
                return False, "CSV 无数据行"
            return True, None
        except Exception as e:  # noqa: BLE001
            return False, f"CSV 解析失败: {e}"
    if suffix in _TEXT_SUFFIXES:
        try:
            data.decode("utf-8")
            return True, None
        except UnicodeDecodeError:
            return False, "文本文件 UTF-8 解码失败"
    return None, None   # 未知扩展名：仅存在性检查，不做解析校验


def check_data_format(ic: IntegrateContext) -> CheckResult:
    """数据格式：output.artifacts 声明产物的存在性与解析级格式校验。"""
    result = CheckResult(name="data_format", ok=True)
    for mid in ic.module_order:
        contract = ic.module_contracts[mid]
        arts = contract.output_artifacts
        if not arts:
            _add(result, kind="no_artifacts", severity="info", module=mid,
                 message=f"{mid}: 未声明 output.artifacts（只读/无产物模块可忽略；"
                         f"若应有交付物请填写 contract.yaml output.artifacts）")
            continue
        missing_any = False
        for rel in arts:
            target = ic.module_dir(mid) / rel
            if not target.is_file():
                missing_any = True
                _add(result, kind="artifact_missing", severity="error", module=mid, path=rel,
                     message=f"{mid}: 声明产物缺失 {rel}（模块目录: {target}）")
                continue
            ok, err = parse_artifact(target)
            suffix = Path(rel).suffix.lower()
            if ok is None:
                _add(result, kind="artifact_ok", severity="info", module=mid, path=rel,
                     message=f"{mid}: 产物 {rel} 存在（扩展名 {suffix or '无'} 不做解析校验，仅存在性）")
            elif ok:
                _add(result, kind="artifact_ok", severity="info", module=mid, path=rel,
                     message=f"{mid}: 产物 {rel} 存在且格式解析通过（{suffix}）")
            else:
                missing_any = True
                _add(result, kind="artifact_format_error", severity="error", module=mid, path=rel,
                     message=f"{mid}: 产物 {rel} 格式校验失败：{err}")
    result.ok = not result.errors
    return result


# ---------------------------------------------------------------- 跨模块数据依赖

def check_data_dependency(ic: IntegrateContext) -> CheckResult:
    """跨模块数据依赖：B 需要的输入（input.from）是否 A 的 output 声明过。

    - input.from 是模块 id（mXX）→ 对应模块 output.artifacts 必须非空且产物真实存在；
      声明过 → satisfied；未声明 → error（"B 需要 A 的输入，但 A 的 output 未声明过"）。
    - input.from 是 shared/... → 文件必须在 shared/ 存在。
    - input.from 是 mXX/... 相对路径 → 在 modules/<模块目录> 下解析。
    - 其它相对路径 → 依次在 shared/ 与任务根下解析。
    - 任务书依赖未在 input.from 声明消费 → warning（可能为排序/间接依赖或未填报）。
    """
    result = CheckResult(name="data_dependency", ok=True)
    for mid in ic.module_order:
        contract = ic.module_contracts[mid]
        deps = list(ic.task_deps.get(mid, []))
        declared_sources: set = set()
        for src in contract.input_from:
            s = str(src).strip()
            if not s:
                continue
            if _MID_RE.match(s):
                declared_sources.add(s)
                if s not in ic.module_contracts:
                    _add(result, kind="unknown_input_module", severity="warning",
                         module=mid, module_b=s,
                         message=f"{mid}: input.from 引用的模块 {s} 不在任务书中（未知输入源）")
                    continue
                prod_arts = ic.module_contracts[s].output_artifacts
                if not prod_arts:
                    _add(result, kind="input_not_declared", severity="error",
                         module=mid, module_b=s,
                         message=(f"{mid} 需要 {s} 的输入（input.from），"
                                  f"但 {s} 的 contract.yaml 未声明 output.artifacts"
                                  f"（B 需要的输入，A 的 output 未声明过）"))
                    continue
                missing_art = [a for a in prod_arts
                               if not (ic.module_dir(s) / a).is_file()]
                if missing_art:
                    _add(result, kind="producer_artifact_missing", severity="error",
                         module=mid, module_b=s, path=missing_art[0],
                         message=(f"{mid} 依赖 {s} 的产物，但 {s} 声明产物缺失其中一个："
                                  f"{', '.join(missing_art)}"))
                else:
                    _add(result, kind="dependency_satisfied", severity="info",
                         module=mid, module_b=s,
                         message=f"{mid} ← {s}：{s} 已声明 output.artifacts 且产物存在，依赖满足")
            elif s.startswith("shared/"):
                declared_sources.add(s)
                target = ic.task_root / s
                _file_source_finding(result, mid, s, target, kind="shared_file")
            elif _ANY_MID_IN_PATH.search(s):
                declared_sources.add(s)
                target = ic.task_root / "modules" / s
                _file_source_finding(result, mid, s, target, kind="module_artifact_file")
            else:
                # 相对路径：shared/ 或任务根下
                declared_sources.add(s)
                t1 = ic.task_root / "shared" / s
                t2 = ic.task_root / s
                if t1.is_file():
                    _add(result, kind="shared_file_ok", severity="info",
                         module=mid, path=s, message=f"{mid}: 输入 {s} 存在于 shared/")
                elif t2.is_file():
                    _add(result, kind="root_file_ok", severity="info",
                         module=mid, path=s, message=f"{mid}: 输入 {s} 存在于任务根")
                else:
                    _add(result, kind="input_file_missing", severity="error",
                         module=mid, path=s,
                         message=f"{mid}: 输入 {s} 不存在（shared/ 与任务根均未找到）")
        for d in deps:
            if d not in declared_sources:
                _add(result, kind="undeclared_consumption", severity="warning",
                     module=mid, module_b=d,
                     message=(f"{mid} 依赖 {d}（任务书声明）但 contract.yaml input.from 未声明消费"
                              f"（可能为排序/间接依赖，或 executor 未填报）"))
    result.ok = not result.errors
    return result


def _file_source_finding(result: CheckResult, mid: str, src: str,
                         target: Path, kind: str) -> None:
    if target.is_file():
        _add(result, kind=f"{kind}_ok", severity="info", module=mid, path=src,
             message=f"{mid}: 输入 {src} 存在（{target}）")
    else:
        _add(result, kind=f"{kind}_missing", severity="error", module=mid, path=src,
             message=f"{mid}: 输入 {src} 缺失（{target}）")
