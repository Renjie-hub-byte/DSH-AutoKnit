"""fw-scaffold 主逻辑：读合法 task.yaml → 生成 v2 目录树（全部文件 fs 原子写）。

流程：
1. 用 fw-protocol validate_file() 校验输入 task.yaml（errors 非空 → 拒绝生成，退出码 1；
   conflicts 不阻塞生成，但随结果返回并醒目提示——目录结构与优先级无关）。
2. effective = result.effective（默认值补全后的任务书）→ 派生：任务目录名/模块目录名/
   模块级任务书/contracts/api.yaml/skeleton.md/总日志初始文件/模板文件。
3. expected 版本防护（guard_existing_dir）→ 全新/幂等/forced 三种路径。
4. 每个文件用 atomic_write_text 写入（同目录临时 + fsync + os.replace）→ 记录 sha256。
5. 最后写 .scaffold-manifest.json（含 task 指纹 + 全部生成文件 hash）作为版本守卫依据。

返回 ScaffoldResult：root / 相对路径清单 / warnings / conflicts / guard_status。
"""
from __future__ import annotations

import copy
import datetime as _dt
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .derive import (
    derive_module_book,
    effective_yaml,
    module_dir_name,
    task_dir_name,
)
from .io_utils import (
    SCAFFOLD_VERSION,
    atomic_write_text,
    guard_existing_dir,
    sha256_file,
    write_guard_manifest,
)
from .templates import (
    auditor_ignore,
    cognition_readme,
    contract_api_yaml,
    contract_yaml,
    data_contract_yaml,
    delivery_md,
    dispatch_init,
    gitkeep,
    integration_init,
    review_md,
    shared_readme,
    skeleton_md,
    snapshot_init,
)

# fw-protocol 复用：framework-v1/fw-protocol 是兄弟包，直接加 sys.path 导入（免 pip install）
_FW1_ROOT = Path(__file__).resolve().parent.parent.parent
_FW_PROTOCOL_DIR = _FW1_ROOT / "fw-protocol"
if str(_FW_PROTOCOL_DIR) not in sys.path:
    sys.path.insert(0, str(_FW_PROTOCOL_DIR))

from fw_protocol import validate_file  # noqa: E402


class TaskInvalidError(Exception):
    """task.yaml 未通过 fw-protocol 校验（errors 非空）。"""


@dataclass
class ScaffoldResult:
    """一次 scaffold 的结果（机器可解析，供 CLI/tests/runner 消费）。"""

    root: Path
    created: bool = False
    guard_status: str = "fresh"          # fresh | idempotent | forced
    files: List[str] = field(default_factory=list)   # 相对路径清单
    directories: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)
    task_name: str = ""
    status: str = "created"              # created | idempotent | conflict

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "status": self.status,
            "root": str(self.root),
            "task_name": self.task_name,
            "guard_status": self.guard_status,
            "files": self.files,
            "directories": self.directories,
            "warnings": self.warnings,
            "conflicts": self.conflicts,
        }


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _iter_modules(effective: Mapping[str, Any]) -> List[Dict[str, Any]]:
    modules = effective.get("modules")
    return [m for m in modules if isinstance(m, dict)] if isinstance(modules, list) else []


def _collect_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _build_plan(effective: Mapping[str, Any], task_name: str
                ) -> Tuple[Dict[str, str], List[str], List[str]]:
    """预生成全部文件内容（relpath → content）与目录清单。纯内存，不落盘。"""
    modules = _iter_modules(effective)
    integ = effective.get("integration") or {}
    contract_file_rel = str(integ.get("contract_file", "contracts/api.yaml") or "")
    # 契约文件必须落在任务根下（contracts/api.yaml 默认）；若配了别的相对路径，保留（不越界检查）
    now = _now()

    files: Dict[str, str] = {}
    dirs: List[str] = []

    # ---- 顶层 ----
    files["task.yaml"] = effective_yaml(effective, task_name)
    contract_rel = contract_file_rel if not contract_file_rel.startswith("/") else "contracts/api.yaml"
    files[contract_rel] = contract_api_yaml(effective)
    # 数据契约区（可选增强）：task.data_contract 非空才生成 contracts/data.yaml，全模块共享同一份
    task_meta = effective.get("task") or {}
    data_contract = task_meta.get("data_contract") or {}
    if data_contract:
        files["contracts/data.yaml"] = data_contract_yaml(effective, task_name)
        dirs += ["contracts"]
    files["skeleton.md"] = skeleton_md(effective)
    files["认知/README.md"] = cognition_readme()
    files["shared/README.md"] = shared_readme()
    files["shared/.readonly"] = gitkeep()
    dirs += ["认知", "shared", "总日志", "modules"]

    # ---- 总日志（初始化：dispatch/integration/快照；执行期更新归 runner/integrate）----
    files["总日志/dispatch.jsonl"] = dispatch_init(task_name, now, dirs)
    files["总日志/integration.jsonl"] = integration_init(task_name, now)
    files["总日志/快照.json"] = snapshot_init(task_name, modules, now)

    # ---- 模块 ----
    for m in modules:
        mid = str(m.get("id", ""))
        mdir = module_dir_name(m)
        base = f"modules/{mdir}"
        dirs.append(base)
        for sub in ("src", "test"):
            files[f"{base}/{sub}/.gitkeep"] = gitkeep()
        for sub in ("logs", "tmp"):
            files[f"{base}/{sub}/.auditor-ignore"] = auditor_ignore(f"{sub}/")
        files[f"{base}/REVIEW.md"] = review_md(m, task_name)
        files[f"{base}/contract.yaml"] = contract_yaml(m, task_name, data_contract=data_contract or None)
        files[f"{base}/任务书-{mid}.yaml"] = derive_module_book(effective, m, modules)
        files[f"{base}/交付说明.md"] = delivery_md(m, task_name)

    return files, dirs, modules


def generate(task_yaml: str | Path,
             output_dir: str | Path = ".",
             force: bool = False,
             dry_run: bool = False,
             today: _dt.date | None = None) -> ScaffoldResult:
    """入口：校验 task.yaml → 生成 v2 目录树。dry_run=True 只返回计划不落盘。"""
    src = Path(task_yaml)
    out = Path(output_dir)
    result = validate_file(src)
    if not result.ok:
        raise TaskInvalidError(result)

    effective = result.effective
    task_name = str((effective.get("task") or {}).get("name", "?"))
    root = out / task_dir_name(effective, today)

    files, dirs, _modules = _build_plan(effective, task_name)
    rels = sorted(files.keys())

    result_obj = ScaffoldResult(
        root=root,
        task_name=task_name,
        files=rels,
        directories=dirs,
        warnings=[i.message for i in result.warnings],
        conflicts=[i.message for i in result.conflicts],
    )

    if dry_run:
        return result_obj

    guard = guard_existing_dir(root, files["task.yaml"], force)
    result_obj.guard_status = guard
    result_obj.created = guard in ("fresh", "forced")
    result_obj.status = "created" if guard in ("fresh", "forced") else "idempotent"

    # 全部文件 fs 原子写（先建目录，再逐文件原子替换）
    for rel in rels:
        target = root / rel
        _collect_dir(target.parent)
        atomic_write_text(target, files[rel])

    # 最后写版本守卫清单（含 task 指纹 + 全部生成文件 hash）
    hashes = {rel: sha256_file(root / rel) for rel in rels if (root / rel).exists()}
    write_guard_manifest(root, files["task.yaml"], hashes, _now())
    return result_obj


def scaffold_task(task_yaml: str | Path, output_dir: str | Path = ".",
                  force: bool = False, dry_run: bool = False) -> ScaffoldResult:
    """别名入口（与 generate 相同；语义化命名供 runner 调用）。"""
    return generate(task_yaml, output_dir=output_dir, force=force, dry_run=dry_run)
