"""fw-integrate 集成上下文：契约源加载（需求6 输入）。

数据来源（全部来自已审计模块的真实产物，只读消费，零源码修改）：
- fw-protocol（round_001）：`validate_file(task.yaml).effective` —— 默认值补全后的任务书
  （task.prediction_baseline / modules[].dependencies / integration.contract_file / runtime.end_gate
  是集成验收的语义输入）。
- fw-scaffold（round_002）生成的 v2 目录树：
  - `contracts/api.yaml` —— 契约区：所有模块接口协议汇总（path/method/note，集成运行时校验基线）
  - `modules/mXX-<名>/contract.yaml` —— 各模块接口契约（input.from / output.artifacts /
    read_api，executor 执行期填写）
  - `modules/mXX-<名>/REVIEW.md` —— 验收闭环（status 键，机器可解析）
- fw-runner（round_004）写回的 `总日志/快照.json` —— run_id / 模块状态 / completed_order。

适配点（诚实标注）：真实 dsh 环境用 session-query 查询各模块契约快照与产物清单；本模块以
文件系统读取为本地等价物（同 fw-runner EventLog 思路）。接入点在 `IntegrateContext.loader`
可替换，默认是 `_FsContractLoader`。
"""
from __future__ import annotations

import sys
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

# 复用兄弟包（与 fw-runner/fw-budget 同款 sys.path 引导，免 pip install）
_FW1_ROOT = Path(__file__).resolve().parent.parent.parent
for _d in ("fw-protocol", "fw-scaffold", "fw-runner", "fw-budget"):
    _p = str((_FW1_ROOT / _d).resolve())
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fw_protocol import validate_file  # noqa: E402
from fw_protocol.model import ValidationResult  # noqa: E402

ENCODING = "utf-8"
MODULE_DIR_RE = re.compile(r"^m\d+-")


class IntegrateInputError(Exception):
    """集成输入不可用（任务根/契约/产物缺失、任务书非法）。"""


def _upper_methods(method: Any) -> set:
    """method 字段归一化为大写集合（'GET' / ['post','put'] → {'POST','PUT'}）。"""
    if isinstance(method, str):
        return {method.strip().upper()} if method.strip() else set()
    if isinstance(method, (list, tuple)):
        out = set()
        for m in method:
            if isinstance(m, str) and m.strip():
                out.add(m.strip().upper())
        return out
    return set()


@dataclass
class ModuleContract:
    """module/contract.yaml 的结构化视图（executor 执行期填写的运行时契约）。"""

    module: str
    name: str = ""
    input_from: List[str] = field(default_factory=list)
    input_describe: str = ""
    output_artifacts: List[str] = field(default_factory=list)
    output_describe: str = ""
    read_api: List[Dict[str, Any]] = field(default_factory=list)   # [{path, method, note}]
    files: List[str] = field(default_factory=list)                 # contract.yaml 涉及的文件路径（相对模块目录）

    @property
    def read_api_pairs(self) -> set:
        """read_api 归一化接口对集合 {(path, METHOD)}。"""
        out: set = set()
        for it in self.read_api:
            path = str(it.get("path") or "").strip()
            if not path:
                continue
            for m in _upper_methods(it.get("method")):
                out.add((path, m))
        return out

    def to_dict(self) -> Dict[str, Any]:
        return {
            "module": self.module, "name": self.name,
            "input": {"from": list(self.input_from), "describe": self.input_describe},
            "output": {"artifacts": list(self.output_artifacts), "describe": self.output_describe},
            "read_api": list(self.read_api),
        }


@dataclass
class IntegrateContext:
    """一次集成验收的完整上下文（全部只读加载）。"""

    task_root: Path
    effective: Dict[str, Any]                          # fw-protocol effective 任务书
    task_name: str
    contract_area: List[Dict[str, Any]]                # contracts/api.yaml 的 api 列表（归一化）
    module_order: List[str]                            # 模块 id 顺序（任务书声明序）
    module_contracts: Dict[str, ModuleContract]        # id -> contract.yaml 视图
    task_deps: Dict[str, List[str]]                    # id -> dependencies（effective）
    module_review_status: Dict[str, str]               # id -> REVIEW.md status 键
    snapshot: Dict[str, Any]                           # 总日志/快照.json（可能为空 dict）
    end_gate: str = "auto"                             # effective.runtime.end_gate
    contract_file: str = "contracts/api.yaml"          # effective.integration.contract_file

    def module_dir(self, mid: str) -> Path:
        """模块目录：按 'mXX-' 前缀匹配（目录规范 v2，与 fw-runner 同款）。"""
        base = self.task_root / "modules"
        for entry in sorted(base.iterdir()):
            if entry.is_dir() and MODULE_DIR_RE.match(entry.name) and entry.name.split("-", 1)[0] == mid:
                return entry
        raise IntegrateInputError(f"modules/ 下找不到模块目录 {mid}（{base}）")

    def review_status(self, mid: str) -> str:
        return self.module_review_status.get(mid, "")

    def all_checks_on(self) -> Dict[str, bool]:
        """integration.check.* 开关（默认 true；false 则跳过对应程序检查 —— 语义同 fw-protocol）。"""
        integ = self.effective.get("integration") or {}
        check = integ.get("check") if isinstance(integ, dict) else None
        if not isinstance(check, dict):
            return {"interface": True, "data_format": True,
                    "cross_module_data_dependency": True, "prediction_baseline": True}
        return {
            "interface": check.get("interface_duplicate", True),
            "data_format": True,   # 数据格式是集成运行时的固有职责，无关闭开关（文档说明）
            "cross_module_data_dependency": check.get("cross_module_data_dependency", True),
            "prediction_baseline": check.get("prediction_baseline", True),
        }


# ---------------------------------------------------------------- 文件加载

def _load_yaml_doc(path: Path) -> Dict[str, Any]:
    import yaml
    try:
        text = path.read_text(encoding=ENCODING)
    except OSError as e:
        raise IntegrateInputError(f"读取 {path} 失败: {e}") from e
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise IntegrateInputError(f"YAML 解析失败 {path}: {e}") from e
    return doc if isinstance(doc, dict) else {}


def _load_contract_area(path: Path) -> List[Dict[str, Any]]:
    """contracts/api.yaml → 归一化 api 列表 [{module, path, method, note}]。"""
    doc = _load_yaml_doc(path)
    api = doc.get("api")
    if not isinstance(api, list):
        raise IntegrateInputError(f"契约区 {path} 缺少 api 列表")
    out: List[Dict[str, Any]] = []
    for it in api:
        if not isinstance(it, dict):
            continue
        module = str(it.get("module") or "")
        path_s = str(it.get("path") or "").strip()
        methods = _upper_methods(it.get("method"))
        if not module or not path_s:
            continue
        for m in sorted(methods):
            out.append({"module": module, "path": path_s, "method": m,
                        "note": str(it.get("note") or "")})
    return out


def _load_module_contract(module_dir: Path) -> ModuleContract:
    """module/contract.yaml → ModuleContract（input/output/read_api）。"""
    p = module_dir / "contract.yaml"
    if not p.is_file():
        raise IntegrateInputError(f"模块缺少 contract.yaml: {p}")
    doc = _load_yaml_doc(p)
    mid = str(doc.get("module") or module_dir.name.split("-", 1)[0])
    ins = doc.get("input")
    outs = doc.get("output")
    read_api_raw = doc.get("read_api")
    read_api: List[Dict[str, Any]] = []
    if isinstance(read_api_raw, list):
        for it in read_api_raw:
            if not isinstance(it, dict):
                continue
            entry = {"path": str(it.get("path") or "").strip()}
            entry["method"] = sorted(_upper_methods(it.get("method")))
            if it.get("note") is not None:
                entry["note"] = str(it.get("note"))
            read_api.append(entry)
    if isinstance(ins, dict) and isinstance(ins.get("from"), list):
        input_from = [str(x).strip() for x in ins["from"] if x]
    else:
        input_from = []
    if isinstance(outs, dict) and isinstance(outs.get("artifacts"), list):
        output_artifacts = [str(x).strip() for x in outs["artifacts"] if x]
    else:
        output_artifacts = []
    return ModuleContract(
        module=mid,
        name=str(doc.get("name") or ""),
        input_from=input_from,
        input_describe=str(ins.get("describe") or "") if isinstance(ins, dict) else "",
        output_artifacts=output_artifacts,
        output_describe=str(outs.get("describe") or "") if isinstance(outs, dict) else "",
        read_api=read_api,
        files=[str(p)],
    )


def _load_review_status(review_path: Path) -> str:
    """REVIEW.md status 键（机器可解析行）；缺文件/无键 → "unknown"。"""
    try:
        from fw_runner.review import read_review
        return str(read_review(review_path).kv.get("status") or "")
    except Exception:
        return "unknown"


def _load_snapshot(task_root: Path) -> Dict[str, Any]:
    p = task_root / "总日志" / "快照.json"
    if not p.is_file():
        return {}
    import json
    try:
        doc = json.loads(p.read_text(encoding=ENCODING))
        return doc if isinstance(doc, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _resolve_contract_area_path(task_root: Path, effective: Mapping[str, Any]) -> Path:
    """integration.contract_file 解析：任务书相对路径 → 任务根下绝对路径。"""
    integ = effective.get("integration")
    cf = (integ.get("contract_file") if isinstance(integ, dict) else None) or "contracts/api.yaml"
    p = Path(str(cf))
    if not p.is_absolute():
        p = task_root / p
    return p


def load_integrate_context(task_root: str | Path, *, require_complete: bool = False) -> IntegrateContext:
    """加载集成上下文（只读）。require_complete=True 时要求快照存在且全部模块完成
    （complete/archive CLI 用；check 可放宽）。

    - 任务书校验失败（fw-protocol errors 非空）→ IntegrateInputError（exit 1 语义）。
    - 合同 area 缺失/非法 → IntegrateInputError。
    """
    root = Path(task_root).expanduser().resolve()
    task_yaml = root / "task.yaml"
    if not task_yaml.is_file():
        raise IntegrateInputError(f"任务根找不到 task.yaml（应先用 fw-scaffold 生成）：{task_yaml}")
    vr: ValidationResult = validate_file(task_yaml)
    if not vr.ok:
        issues = [i.message for i in vr.errors]
        raise IntegrateInputError(
            f"任务书复校验失败（fw-protocol），拒绝集成验收：{len(issues)} 个 error\n"
            + "\n".join(f"  - {m}" for m in issues[:20]))
    effective = vr.effective or {}
    task_name = str((effective.get("task") or {}).get("name", "?"))
    modules = [m for m in (effective.get("modules") or []) if isinstance(m, dict)]
    module_order = [str(m["id"]) for m in modules]
    task_deps = {str(m["id"]): [d for d in (m.get("dependencies") or []) if isinstance(d, str)]
                 for m in modules}

    contract_path = _resolve_contract_area_path(root, effective)
    if not contract_path.is_file():
        raise IntegrateInputError(f"契约区缺失（integration.contract_file={contract_path}，应 fw-scaffold 生成）")
    contract_area = _load_contract_area(contract_path)

    module_contracts: Dict[str, ModuleContract] = {}
    review_status: Dict[str, str] = {}
    for mid in module_order:
        mdir = root / "modules"
        found = None
        for entry in sorted(mdir.iterdir()):
            if entry.is_dir() and MODULE_DIR_RE.match(entry.name) and entry.name.split("-", 1)[0] == mid:
                found = entry
                break
        if found is None:
            raise IntegrateInputError(f"modules/ 下找不到模块目录 {mid}（应 fw-scaffold 生成）：{mdir}")
        module_contracts[mid] = _load_module_contract(found)
        review_status[mid] = _load_review_status(found / "REVIEW.md")

    snapshot = _load_snapshot(root)
    if require_complete:
        if not snapshot:
            raise IntegrateInputError("找不到 总日志/快照.json（从未运行过？先 fw-runner run 再集成验收）")
        snap_status = str(snapshot.get("status") or "")
        if snap_status != "complete":
            raise IntegrateInputError(
                f"集成完成/归档要求全部模块完成（快照 status={snap_status}，需 complete；"
                f"先 fw-runner run 跑完所有模块再 fw-integrate complete）")
        completed = [str(x) for x in (snapshot.get("completed_order") or [])]
        missing = [mid for mid in module_order if mid not in completed]
        if missing:
            raise IntegrateInputError(f"快照显示模块未全部完成：{', '.join(missing)} 不在 completed_order")

    rt = effective.get("runtime") or {}
    end_gate = str(rt.get("end_gate") or "auto")
    return IntegrateContext(
        task_root=root, effective=effective, task_name=task_name,
        contract_area=contract_area, module_order=module_order,
        module_contracts=module_contracts, task_deps=task_deps,
        module_review_status=review_status, snapshot=snapshot,
        end_gate=end_gate,
        contract_file=str(contract_path),
    )
