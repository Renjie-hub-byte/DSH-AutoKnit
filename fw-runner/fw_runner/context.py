"""运行上下文加载：复用 fw-protocol 校验结果 + fw-scaffold 目录形状解析。

- validate_file(root/task.yaml).effective —— 默认值补全后的任务书（errors 非空 → 拒绝运行）
- modules/ 目录扫描：目录名 mXX-<名> 前缀与模块 id 匹配（目录规范 v2）
- RunConfig 解析：effective.runtime 默认值 + CLI 覆盖 + 模式开关（speed_first/cost_first）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

# fw-protocol 为正规 pip 依赖（pyproject.toml 声明），标准 import，不再 sys.path hack
from fw_protocol import validate_file

from .model import ModuleSpec, RunConfig, now_iso

# fw-runner 源码根（fw_runner 包的上一级）；仅用于未 pip install 的源码树直跑场景
# （drivers._pythonpath 注入子进程）。pip 安装后该路径无意义但无害。
_RUNNER_SOURCE_ROOT = Path(__file__).resolve().parent.parent

SNAPSHOT_REL = "总日志/快照.json"
DISPATCH_REL = "总日志/dispatch.jsonl"
INTEGRATION_REL = "总日志/integration.jsonl"


class RunnerInputError(Exception):
    """任务根/任务书不可运行（校验失败、目录缺失、配置非法）。"""


@dataclass
class TaskContext:
    """运行上下文：任务根 + effective 任务书 + 模块规格 + 运行配置 + 依赖边。"""

    task_root: Path
    effective: Dict[str, Any]
    task_name: str
    modules: Dict[str, ModuleSpec]       # id -> spec（按 effective.modules 顺序稳定）
    module_order: List[str]              # id 列表（任务书声明顺序）
    dependencies: Dict[str, List[str]]   # id -> deps（不含未知依赖，有效任务书必合法）
    config: RunConfig
    run_dir: Path                        # 任务根（= task_root；总日志/ 在其下）

    def task_yaml(self) -> Path:
        return self.task_root / "task.yaml"

    def snapshot_path(self) -> Path:
        return self.task_root / SNAPSHOT_REL

    def dispatch_path(self) -> Path:
        return self.task_root / DISPATCH_REL

    def integration_path(self) -> Path:
        return self.task_root / INTEGRATION_REL


def _resolve_runtime_config(effective: Mapping[str, Any],
                            overrides: Optional[Mapping[str, Any]] = None,
                            mode: str = "speed_first") -> RunConfig:
    """effective.runtime（默认值已补全）+ CLI 覆盖 + 模式开关 → RunConfig。

    模式开关（需求7 文档化；runner 层先落地可测最小策略）：
    - speed_first（默认）：max_parallel 用 runtime 原值，追求吞吐。
    - cost_first：省 token/会话 → 并行上限压到 min(max_parallel, 2)，提高同 executor
      打回耐心（retry_before_switch + 1，少换人少开销）。完整策略差异留需求7。
    显式 CLI 覆盖优先级最高（overrides 非空即生效，不受模式影响）。
    """
    rt = effective.get("runtime") or {}
    overrides = dict(overrides or {})
    cfg = RunConfig(
        max_parallel=int(rt.get("max_parallel", 3)),
        executor_max_rounds=int(rt.get("executor_max_rounds", 5)),
        retry_before_switch=int(rt.get("retry_before_switch", 2)),
        max_executor_switches=int(rt.get("max_executor_switches", 1)),
        end_gate=str(rt.get("end_gate", "auto")),
        models=dict((rt.get("models") or {})),
        mode=mode,
        overrides=dict(overrides),
    )
    if mode == "cost_first":
        cfg.max_parallel = min(cfg.max_parallel, 2)
        cfg.retry_before_switch = cfg.retry_before_switch + 1
    for key in ("max_parallel", "executor_max_rounds", "retry_before_switch",
                "max_executor_switches", "end_gate", "heartbeat_n_rounds",
                "checkpoint_every", "mode", "split_max_depth",
                "split_exit_threshold", "retry_remaining_threshold",
                "audit_require_evidence", "enable_split"):
        if key in overrides and overrides[key] is not None:
            val = overrides[key]
            if key == "end_gate":
                if val not in ("auto", "always"):
                    raise RunnerInputError(f"--end-gate 仅支持 auto|always，收到 {val!r}")
                setattr(cfg, key, val)
            elif key == "mode":
                if val not in ("speed_first", "cost_first"):
                    raise RunnerInputError(f"--mode 仅支持 speed_first|cost_first，收到 {val!r}")
                cfg.mode = val
            elif key == "audit_require_evidence":
                # BUG-002a（2026-08-25）：bool 键单独转换，支持字符串 "true"/"false"
                if isinstance(val, str):
                    cfg.audit_require_evidence = val.strip().lower() in ("1", "true", "yes", "on")
                else:
                    cfg.audit_require_evidence = bool(val)
            elif key == "enable_split":
                # 2026-08-28：bool 键（CLI --no-split / --enable-split）
                if isinstance(val, str):
                    cfg.enable_split = val.strip().lower() in ("1", "true", "yes", "on")
                else:
                    cfg.enable_split = bool(val)
            else:
                setattr(cfg, key, int(val))
    # 心跳与 checkpoint 参数也允许 CLI 覆盖（不来自任务书，属 runner 级配置）
    if overrides.get("heartbeat_n_rounds") is not None:
        cfg.heartbeat_n_rounds = int(overrides["heartbeat_n_rounds"])
    if overrides.get("checkpoint_every") is not None:
        cfg.checkpoint_every = int(overrides["checkpoint_every"])
    if cfg.max_parallel < 1:
        raise RunnerInputError("max_parallel 必须 >= 1")
    if cfg.executor_max_rounds < 1 or cfg.retry_before_switch < 1:
        raise RunnerInputError("executor_max_rounds / retry_before_switch 必须 >= 1")
    if cfg.max_executor_switches < 0:
        raise RunnerInputError("max_executor_switches 必须 >= 0")
    return cfg


def _iter_modules(effective: Mapping[str, Any]) -> List[Dict[str, Any]]:
    modules = effective.get("modules")
    return [m for m in modules if isinstance(m, dict)] if isinstance(modules, list) else []


def _resolve_module_dirs(task_root: Path, modules: Sequence[Mapping[str, Any]]) -> Dict[str, Path]:
    """扫描 modules/ 目录，按 'mXX-' 前缀匹配模块 id → 模块目录。

    以脚手架真实产物为准（不自行推算目录名，避免命名规则漂移）。
    """
    base = task_root / "modules"
    if not base.is_dir():
        raise RunnerInputError(f"任务根缺少 modules/ 目录（应先用 fw-scaffold 生成）：{base}")
    found: Dict[str, Path] = {}
    for entry in sorted(base.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        mid = entry.name.split("-", 1)[0]
        if mid:
            found.setdefault(mid, entry)
    missing = [m.get("id") for m in modules if m.get("id") not in found]
    if missing:
        raise RunnerInputError(
            f"modules/ 下找不到以下模块目录（缺 {', '.join(missing)}；需先跑 fw-scaffold 生成）: {base}")
    return {m["id"]: found[m["id"]] for m in modules if m.get("id") in found}


def load_task_context(task_root: str | Path,
                      overrides: Optional[Mapping[str, Any]] = None,
                      mode: str = "speed_first",
                      require_valid: bool = True) -> TaskContext:
    """加载任务根上下文。require_valid=False 时（resume 场景）跳过 fw-protocol 复校验的强错误。"""
    root = Path(task_root).expanduser().resolve()
    task_yaml = root / "task.yaml"
    if not task_yaml.is_file():
        raise RunnerInputError(f"任务根找不到 task.yaml（应先用 fw-scaffold 生成 v2 目录树）：{task_yaml}")
    try:
        result = validate_file(task_yaml)
    except Exception as e:  # YAML 解析/IO 异常 → 输入错误（exit 1）
        raise RunnerInputError(f"任务书解析失败（fw-protocol）: {type(e).__name__}: {e}") from e
    if require_valid and not result.ok:
        issues = [i.message for i in result.errors]
        raise RunnerInputError(
            f"任务书复校验失败（fw-protocol），拒绝运行：{len(result.errors)} 个 error\n"
            + "\n".join(f"  - {m}" for m in issues[:20]))
    effective = result.effective or {}
    task_name = str((effective.get("task") or {}).get("name", "?"))
    raw_modules = _iter_modules(effective)
    dep_map = {m["id"]: [d for d in (m.get("dependencies") or []) if isinstance(d, str)]
               for m in raw_modules}
    dir_map = _resolve_module_dirs(root, raw_modules)
    cfg = _resolve_runtime_config(effective, overrides=overrides, mode=mode)

    specs: Dict[str, ModuleSpec] = {}
    order: List[str] = []
    for m in raw_modules:
        mid = str(m["id"])
        spec = ModuleSpec(
            id=mid,
            name=str(m.get("name") or mid),
            layer=int(m.get("layer") or 1),
            objective=str(m.get("objective") or ""),
            dependencies=list(dep_map.get(mid, [])),
            dir=dir_map[mid],
            review_path=dir_map[mid] / "REVIEW.md",
            contract_path=dir_map[mid] / "contract.yaml",
            book_path=dir_map[mid] / f"任务书-{mid}.yaml",
            delivery_path=dir_map[mid] / "交付说明.md",
        )
        specs[mid] = spec
        order.append(mid)
    return TaskContext(task_root=root, effective=effective, task_name=task_name,
                       modules=specs, module_order=order, dependencies=dep_map,
                       config=cfg, run_dir=root)
