"""split 模块（v1.0）：收集上下文 + 调 split agent + 落地子模块 + 共享上下文 + 入队。

对应设计文档《设计-v1.0-简化版.md》第五节（SPLIT 环节）与《工程对接清单-v1.0.md》二.2。
本轮只落地 split.py 的公开函数（D1–D5），runner 侧 `_do_split` 集成留 C 轮。

- D1 collect_split_context  : 收集 5 项输入（objective / 完整 deliverables / auditor
                              判定 / REVIEW / 已完成文件列表）
- D2 call_split_agent        : 调 split agent（DSH flash，一次性）→ 拆解 JSON + 校验
- D3 scaffold_children       : 落地子模块标准目录（modules/ 平级）+ 注册 ModuleSpec
- D4 generate_shared_context : 生成父模块目录 SHARED_CONTEXT.md
- D5 insert_children_into_order : 子模块插入 module_order（父模块后）+ 依赖图更新

拆解 JSON 协议（设计文档第五节 / prompts/split.md）：
  action / parent_module / new_modules[] / dependency_map / context_from_parent
  new_modules[]: id / name / objective / deliverables / files
  action == "cannot_split" → 失败语义（不硬拆，回人）
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from importlib.resources import files as _resource_files
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .context import TaskContext
from .drivers import AgentContext, ScriptedAgentDriver, _read_outcome_json
from .io_utils import atomic_write_text
from .model import DriverOutcome, ModuleSpec, RunState


def _package_file(rel: str) -> Path:
    """解析 fw_runner 包内资源为真实文件路径（安装后同样可用，不依赖 monorepo 布局）。"""
    return Path(str(_resource_files("fw_runner").joinpath(rel)))


_SPLIT_SCRIPT = _package_file("scripts/fw-split.sh")   # 包内数据文件（env FW_SPLIT_SCRIPT 可覆盖）
_SPLIT_PROMPT = _package_file("prompts/split.md")      # 包内提示词（env FW_SPLIT_PROMPT 可覆盖）

SPLIT_ROLE = "split"
SPLIT_OUTCOME_REL = "tmp/split-outcome.json"   # 协议：退出码 0 + 该文件存在 → 成功
SPLIT_CONTEXT_REL = "tmp/split-context.json"   # runner 收集的 5 项输入，喂给 split 脚本
SHARED_CONTEXT_NAME = "SHARED_CONTEXT.md"

# v2 拆解 JSON 必需字段（prompts/split.md，贪心单块递归）
REQUIRED_TOP = ("action", "parent_module", "next_block", "remaining_after", "dependency_map")
REQUIRED_NEXT = ("id", "name", "objective", "deliverables", "files")

DEFAULT_SPLIT_MODEL = "deepseek-v4-flash"   # split agent 固定 flash（设计文档十六：全角色 flash）


class SplitCallError(Exception):
    """调 split agent 失败（非零退出 / 中断 / 无拆解产物）。"""


class SplitJSONError(Exception):
    """拆解 JSON 不符合协议（缺失字段 / 字段非法）。"""


class CannotSplitError(SplitJSONError):
    """split agent 判定剩余一轮能完（action=cannot_split 兜底路径）。

    2026-08-28 语义定稿（杰哥决策）：**小程序剩余不进 cannot_split——提示词只教
    split+收尾块。** cannot_split 存留为模型乱输出的兜底：runner 捕获后由程序
    生成「收尾块」（next_block=剩余全量）下放，不回人、不丢活、不回原 executor 续做。"""


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _sanitize_name(name: str) -> str:
    """目录安全化（与 fw-scaffold derive.sanitize_name 同语义，本地兜底避免硬依赖）。"""
    import re
    if not name:
        return "child"
    s = re.sub(r"[^\w.\-]", "-", str(name), flags=re.UNICODE)
    s = re.sub(r"-+", "-", s)
    s = s.strip("-.")
    return s or "child"


def validate_split_json(data: Any) -> Tuple[bool, List[str]]:
    """校验拆解 JSON（v2 贪心单块协议，prompts/split.md）。

    返回 (ok, errors)。action=cannot_split → (False, [reason])（runner 程序化转收尾块，不硬拆）。
    合法拆解：action == "split"，且 next_block 一块字段齐全。
    remaining_after 允许为空（收尾块语义：remaining_after 空 scope/0 行 = 最后一块，做完即 done）。
    """
    errors: List[str] = []
    if not isinstance(data, dict):
        return False, ["拆解 JSON 必须是对象"]
    action = data.get("action")
    if action == "cannot_split":
        return False, [f"cannot_split: {data.get('reason') or '模块剩余一轮能完（程序转收尾块下放）'}"]
    if action != "split":
        return False, [f"action 必须是 'split' 或 'cannot_split'，收到 {action!r}"]
    for k in REQUIRED_TOP:
        if k not in data:
            errors.append(f"缺少必需字段: {k}")
    nb = data.get("next_block")
    if not isinstance(nb, dict) or not nb:
        errors.append("next_block 必须是非空对象（贪心单块：一次只拆下一块）")
    else:
        for k in REQUIRED_NEXT:
            if k not in nb:
                errors.append(f"next_block.{k} 缺失")
        if not isinstance(nb.get("id"), str) or not nb.get("id", "").strip():
            errors.append("next_block.id 必须是非空字符串")
        if not isinstance(nb.get("objective"), str) or not nb.get("objective", "").strip():
            errors.append("next_block.objective 必须是非空字符串")
        if not isinstance(nb.get("deliverables"), list) or not nb.get("deliverables"):
            errors.append("next_block.deliverables 必须是非空列表")
        if not isinstance(nb.get("files"), list):
            errors.append("next_block.files 必须是列表")
    ra = data.get("remaining_after")
    if not isinstance(ra, dict):
        errors.append("remaining_after 必须是非空对象（收尾块写 scope 空串 + estimate_lines 0）")
    else:
        # 收尾块允许 scope 空：remaining_after 空 = 最后一块（做完即 done，不再递归）
        if ra.get("estimate_lines") is not None:
            try:
                int(ra["estimate_lines"])
            except (TypeError, ValueError):
                errors.append("remaining_after.estimate_lines 必须是整数")
    if not isinstance(data.get("dependency_map"), dict):
        errors.append("dependency_map 必须是对象")
    return (not errors), errors


def _derive_child_book(effective: Mapping[str, Any],
                       child: Mapping[str, Any],
                       all_modules: Sequence[Mapping[str, Any]]) -> str:
    """派生子模块任务书 YAML（复用 fw-scaffold 标准派生，语义一致、字段齐全）。"""
    # fw-scaffold 为正规 pip 依赖（pyproject.toml 声明），标准 import
    from fw_scaffold.derive import derive_module_book
    return derive_module_book(effective, child, all_modules)


def _read_deliverables(ctx: TaskContext, mid: str) -> List[str]:
    """父模块完整 deliverables：任务书模块条目取 deliverables，缺省回落 acceptance
    （v0.4 schema 的验收清单即 auditor 的交付物清单；v1.0 若落盘 deliverables 优先用）。"""
    raw = [m for m in (ctx.effective.get("modules") or []) if isinstance(m, dict) and m.get("id") == mid]
    if not raw:
        return []
    m = raw[0]
    if isinstance(m.get("deliverables"), list) and m["deliverables"]:
        return [str(x) for x in m["deliverables"]]
    if isinstance(m.get("acceptance"), list) and m["acceptance"]:
        return [str(x) for x in m["acceptance"]]
    return []


def _scan_src_files(spec: ModuleSpec) -> List[str]:
    """已完成文件列表：父模块 src/ 产物（绝对路径，递归；跳过隐藏文件）。"""
    files: List[str] = []
    src = spec.dir / "src"
    if src.is_dir():
        for p in sorted(src.rglob("*")):
            if p.is_file() and not p.name.startswith("."):
                files.append(str(p.resolve()))
    return files


def build_wrapup_split_json(ctx: TaskContext, mid: str, context: Mapping[str, Any]) -> Dict[str, Any]:
    """程序化构造「单块」拆解 JSON（cannot_split 兜底，2026-08-28）。

    语义（杰哥定稿）：剩余工作不管多小，都拆成**一块**下放给新 executor 一次做完——
    不回原 executor 续做、不回人、不丢活。next_block = 剩余全量，remaining_after 空。
    deliverables 优先级：auditor remaining_items → REVIEW 待办 → 剩余 scope 兜底。
    """
    remaining_items = [str(x) for x in (context.get("remaining_items") or []) if str(x).strip()]
    review_todo = [str(x) for x in (context.get("review_todo") or []) if str(x).strip()]
    scope = str((context.get("module_remaining") or {}).get("scope") or "")
    if remaining_items:
        deliverables = list(remaining_items)
    elif review_todo:
        deliverables = list(review_todo)
    elif scope:
        deliverables = [f"完成剩余工作：{scope}"]
    else:
        deliverables = [f"{mid} 收尾：把剩余工作做完并自测通过"]
    obj_head = str(context.get("objective") or mid)
    goal = str(context.get("task_goal") or "")
    objective = (f"【{goal}】{mid} 收尾：{scope or '剩余全量'}。"
                 f"这是最后一块，做完全部剩余即收工。父模块已做：{obj_head}")
    child_id = f"{mid}w"
    while child_id in ctx.modules:
        child_id += "w"
    return {
        "action": "split",
        "parent_module": mid,
        "next_block": {
            "id": child_id,
            "name": "剩余全量（收尾）",
            "objective": objective,
            "deliverables": deliverables,
            "files": [],
        },
        "remaining_after": {"scope": "", "estimate_lines": 0},
        "dependency_map": {child_id: []},
        "context_from_parent": (f"父模块 {mid} 已完成主体工作（controller 判定剩余为收尾量级）；"
                                f"本块为最后一块，做完全部剩余即收工。剩余说明：{scope or '(见交付物清单)'}"),
    }


def _audit_mapping(audit: Any) -> Dict[str, Any]:
    """auditor 判定归一：DriverOutcome 或 dict → {passed_count,total_count,remaining_items}。"""
    if isinstance(audit, DriverOutcome):
        return {
            "passed_count": int(audit.passed_count),
            "total_count": int(audit.total_count),
            "remaining_items": list(audit.remaining_items or []),
            "verdict": audit.verdict or "partial",
        }
    d = dict(audit or {})
    remaining = d.get("remaining_items")
    return {
        "passed_count": int(d.get("passed_count") or 0),
        "total_count": int(d.get("total_count") or 0),
        "remaining_items": [str(x) for x in remaining] if isinstance(remaining, list) else [],
        "verdict": str(d.get("verdict") or "partial"),
    }


def collect_split_context(ctx: TaskContext, state: RunState, mid: str,
                          audit: Any = None) -> Dict[str, Any]:
    """D1：收集 split agent 的 5 项输入（设计文档第五节）。

    ① objective  ② 完整 deliverables（含已勾选/未勾选）③ auditor 判定（passed_count /
    total_count / remaining_items）④ REVIEW（已做/待办/问题）⑤ 已完成文件列表（绝对路径）。
    audit 可为 DriverOutcome 或 dict（runner 的 _do_split 传入 aout）；缺省时尝试从
    REVIEW.md 判定键兜底，再缺省用空值（不阻塞拆分）。
    """
    spec = ctx.modules[mid]
    astate = state.ensure(mid)
    review_text = ""
    review_kv: Dict[str, str] = {}
    review_done: List[str] = []
    review_todo: List[str] = []
    if spec.review_path.is_file():
        from .review import read_review
        doc = read_review(spec.review_path)
        review_text = doc.raw
        review_kv = dict(doc.kv)
        review_done = [_clean_done(ln) for ln in doc.list_done() if "（占位）" not in ln]
        review_todo = [_clean_todo(ln) for ln in doc.list_todo() if "（占位）" not in ln]
    if audit is None and review_kv:
        audit = {
            "passed_count": int(review_kv.get("passed_count") or 0),
            "total_count": int(review_kv.get("total_count") or 0),
            "remaining_items": _split_remaining(review_kv.get("remaining_items", "")),
        }
    audit_map = _audit_mapping(audit)
    # 总目标层（v2：split 拆下一块时要带"总的"上下文，next_block.objective 含总目标定位）
    task_meta = dict(ctx.effective.get("task") or {})
    pb = dict(task_meta.get("prediction_baseline") or {})
    parent_rem = dict(mod.get("remaining_estimate") or {}) if (
        mod := next((m for m in (ctx.effective.get("modules") or [])
                     if isinstance(m, dict) and m.get("id") == mid), None)) else {}
    return {
        "mid": mid,
        "parent_module": mid,
        "objective": spec.objective,
        "task_goal": str(task_meta.get("goal") or ""),
        "will_not_have": [str(x) for x in (pb.get("will_not_have") or [])],
        "module_remaining": {
            "scope": str(parent_rem.get("scope") or ""),
            "estimate_lines": parent_rem.get("estimate_lines"),
        },
        "deliverables": _read_deliverables(ctx, mid),           # 完整清单（含未勾选）
        "audit": audit_map,                                     # auditor 最近判定
        "passed_count": audit_map["passed_count"],
        "total_count": audit_map["total_count"],
        "remaining_items": audit_map["remaining_items"],
        "review": review_text,                                  # REVIEW 全文（已做/待办/问题）
        "review_done": review_done,
        "review_todo": review_todo,
        "files": _scan_src_files(spec),                         # 已完成文件（绝对路径）
        "dependencies": list(spec.dependencies or []),
        "split_depth": int(astate.split_depth),
        "executor_round": int(astate.executor_round),
        "model_tier": int(astate.model_tier),
    }


def _clean_done(line: str) -> str:
    return line.strip().lstrip("- ").strip()


def _clean_todo(line: str) -> str:
    return line.strip().lstrip("- [ ]").strip()


def _split_remaining(text: str) -> List[str]:
    if not text:
        return []
    return [x.strip() for x in text.split(",") if x.strip()]


def _normalize_split_json(sj: Optional[Dict[str, Any]], mid: str) -> Optional[Dict[str, Any]]:
    """归一化 split agent 输出的拆解 JSON，对 LLM 嵌套漂移免疫（BUG-20260829）。

    实测 split agent 常把 remaining_after / dependency_map / context_from_parent
    塞进 next_block 内层，或只吐出裸 next_block（缺 action/parent_module），导致
    validate_split_json 报"缺少必需字段"→ 误判"无法拆分"→ 回人（1200 行剩余
    拆不动的假象）。这里自动：
      1) 裸 next_block（有 id 无 action）→ 包成 {action:split, parent_module:mid, next_block}
      2) 内层 remaining_after / dependency_map / context_from_parent 提升到顶层
    """
    if not isinstance(sj, dict):
        return sj
    if "action" not in sj and isinstance(sj.get("id"), str):
        nb = dict(sj)
        sj = {"action": "split", "parent_module": str(mid), "next_block": nb}
    nb = sj.get("next_block")
    if isinstance(nb, dict):
        for k in ("remaining_after", "dependency_map", "context_from_parent"):
            if k not in sj and k in nb:
                sj[k] = nb.pop(k) if isinstance(nb, dict) else nb[k]
    return sj


def _extract_split_json(module: ModuleSpec, aout: DriverOutcome) -> Optional[Dict[str, Any]]:
    """从 driver outcome 提取拆解 JSON：detail.split → detail 自带 action → outcome 文件。

    BUG-20260829 修复：提取后统一过 _normalize_split_json（容错嵌套漂移 + 裸 next_block 包协议）。
    """
    detail = aout.detail if isinstance(aout.detail, dict) else {}
    if isinstance(detail.get("split"), dict):
        return _normalize_split_json(dict(detail["split"]), module.id)
    if "action" in detail:
        return _normalize_split_json(dict(detail), module.id)
    raw = _read_outcome_json(module.dir, SPLIT_ROLE)
    if isinstance(raw, dict):
        inner = raw.get("detail")
        if isinstance(inner, dict) and isinstance(inner.get("split"), dict):
            return _normalize_split_json(dict(inner["split"]), module.id)
        if isinstance(raw.get("action"), str):
            return _normalize_split_json(dict(raw), module.id)
    return None


def _default_split_driver(ctx: TaskContext, env: Optional[Dict[str, str]] = None) -> ScriptedAgentDriver:
    """默认 split 驱动：调包内 scripts/fw-split.sh（cwd=模块目录，一次性 DSH flash 调用）。

    FW_SPLIT_SCRIPT 可覆盖脚本路径；FW_SPLIT_PROMPT 可覆盖提示词路径（见 call_split_agent）。
    """
    script = Path(os.environ.get("FW_SPLIT_SCRIPT") or _SPLIT_SCRIPT)
    cmd = f"bash {script}"
    return ScriptedAgentDriver(cmd=cmd, role=SPLIT_ROLE, env=env)


def call_split_agent(ctx: TaskContext, mid: str, context: Mapping[str, Any],
                     driver: Any = None) -> Dict[str, Any]:
    """D2：调 split agent（DSH flash，一次性），返回校验通过的拆解 JSON。

    - 收集到的 5 项输入先落 tmp/split-context.json（喂给 bin/fw-split.sh，E 轮拼指令）
    - 复用 ScriptedAgentDriver 契约（role=split → 读 tmp/split-outcome.json）
    - 拆解 JSON 校验（validate_split_json）：缺失字段 / cannot_split → SplitJSONError
    """
    module = ctx.modules[mid]
    split_dir = module.dir / "tmp"
    atomic_write_text(split_dir / "split-context.json",
                      json.dumps(dict(context), ensure_ascii=False, indent=2))
    # FW_SPLIT_MODEL 必须是真实模型名：model_tiers 是档位名（flash/pro，非可调用模型），
    # 用它覆盖会写坏 model-patch.yml。split agent 固定 flash → 注入真实模型名 DEFAULT_SPLIT_MODEL。
    # ⚠️ 必须继承 os.environ（含 PATH/DSH_HOME），否则 fw-split.sh 里 python3 等命令找不到 → 退出码 1
    env = dict(os.environ)
    env.update({"FW_SPLIT_MODEL": DEFAULT_SPLIT_MODEL, "FW_SPLIT_ROLE": SPLIT_ROLE})
    # 提示词默认用包内资源（prompts/split.md 随包分发）；用户显式设置的 FW_SPLIT_PROMPT 优先
    env.setdefault("FW_SPLIT_PROMPT", str(_SPLIT_PROMPT))
    if driver is None:
        driver = _default_split_driver(ctx, env=env)
    actx = AgentContext(
        module=module, run_id="", role=SPLIT_ROLE, round_no=1,
        executor_id=SPLIT_ROLE, task_root=ctx.task_root, mode=ctx.config.mode, env=env,
    )
    aout = driver.run_round(actx)
    if aout.status != "ok":
        raise SplitCallError(f"split agent 调用失败(status={aout.status}): {aout.reason}")
    split_json = _extract_split_json(module, aout)
    if split_json is None:
        raise SplitCallError(f"split agent 无拆解产物（缺 {SPLIT_OUTCOME_REL} / detail.split）")
    if isinstance(split_json, dict) and split_json.get("action") == "cannot_split":
        # 业务分支（2026-08-28）：剩余收尾量级 → CannotSplitError，runner 程序化生成单块下放
        raise CannotSplitError(str(split_json.get("reason") or "剩余一轮可完，程序化单块下放"))
    ok, errors = validate_split_json(split_json)
    if not ok:
        raise SplitJSONError("; ".join(errors))
    return split_json


def _resolve_child_deps(ctx: TaskContext, mid: str, dep_map: Mapping[str, Any],
                        child_id: str) -> List[str]:
    """子模块依赖 = 父模块上游依赖 ∪ dependency_map[child]，剔除父模块自身（防环）。"""
    parent_deps = list(ctx.modules[mid].dependencies or [])
    known = set(ctx.modules.keys())
    deps = [d for d in parent_deps if isinstance(d, str) and d in known and d != mid]
    for d in (dep_map.get(child_id) or []):
        if isinstance(d, str) and d in known and d != mid and d not in deps:
            deps.append(d)
    return sorted(set(deps))


def _child_review(child_id: str, name: str, objective: str, deps: Sequence[str],
                  deliverables: Sequence[str], parent: str) -> str:
    deps_s = ", ".join(deps) if deps else "(无)"
    dl_s = " / ".join(deliverables) if deliverables else "(空)"
    return f"""# REVIEW —— {child_id} {name}（子模块，split 派生）

> 由 split agent 拆分父模块 {parent} 生成。executor 开工先读 SHARED_CONTEXT.md + 本文件。
> 保持机器可解析键值行格式（key: value）。

## 模块元信息
id: {child_id}
name: {name}
split_parent: {parent}
objective: {objective}
dependencies: {deps_s}
deliverables: {dl_s}
status: pending            # 合法值: pending | running | needs_review | blocked | done | split
executor_round: 0
auditor_round: 0

## 待办（todo，executor 开工第一件事：按交付物清单拆可执行 todo）
- [ ] 通读 SHARED_CONTEXT.md 与 任务书-{child_id}.yaml、contract.yaml
- [ ] （占位）按交付物清单逐条列出任务

## 已做（done，按事件 seq 或时间记录，可追溯）
- （占位）

## 问题与根因（失败分类器）
root:
detail:

## 置信度
confidence: 0.0      # auditor 判定置信度 0-1

## 交接说明（换 executor 时读：问题 / 现象 / 已试办法；新 executor 据此续作）
现象:
已试办法:

## 外部验收自测（executor 交付前逐条对照交付物清单）
- （占位，逐条标注 通过/不通过/证据）
"""


def _child_contract(child_id: str, name: str,
                    data_contract: Optional[Mapping[str, Any]] = None) -> str:
    """子模块 contract.yaml：继承任务级共享数据契约（若有），保持全模块一致。"""
    body = f"""# modules/{child_id}-{name}/contract.yaml —— 模块接口契约（split 派生，子模块）

module: {child_id}
name: {name}
split: true
input:
  from: []             # 上游模块产出 / shared/ 文件 / 父模块 SHARED_CONTEXT.md
  describe:            # 一句话描述输入格式（占位，executor 填写）
output:
  artifacts: []        # 产物相对路径列表（如 src/xxx.py）
  describe:            # 一句话描述输出格式（占位）
read_api:
  []                    # 子模块无独立接口声明（继承父模块契约）
"""
    if data_contract:
        import yaml as _yaml
        dc_yaml = _yaml.safe_dump(data_contract, allow_unicode=True, sort_keys=False,
                                  default_flow_style=False, indent=2).rstrip("\n")
        body += "\nshared_data:           # 跨模块共享数据契约（继承任务级；contracts/data.yaml 是唯一事实源）\n"
        body += "\n".join("  " + ln for ln in dc_yaml.splitlines()) + "\n"
    return body


def _child_delivery(child_id: str, name: str, parent: str) -> str:
    return f"""# 交付说明 —— {child_id} {name}（子模块，split 派生）

> 由 split agent 拆分父模块 {parent} 生成。executor 交付报告（改了什么 / 测了什么 / 风险）。
> 生成时间：{_now()}

## 改动内容（改了什么）
- （占位）

## 测试结果（测了什么，含命令与输出摘要）
- （占位）

## 外部验收自测（对照任务书-{child_id}.yaml 的 deliverables 逐条自测）
- （占位）

## 已知风险 / 边界（如实标注限制、未覆盖场景、妥协点）
- （占位）

## 交接备注（如需换 executor / 回人拍板时留给下游的信息）
- （占位）
"""


def _build_child_module_dict(ctx: TaskContext, mid: str, child: Mapping[str, Any],
                             dep_map: Mapping[str, Any],
                             remaining_after: Mapping[str, Any] | None = None) -> Tuple[str, Dict[str, Any]]:
    """子模块任务书条目（id/name/layer/objective/dependencies/deliverables/files/…）。

    remaining 传递链（2026-08-25 重构）：父模块拆出 next_block 后，remaining_after（这块之外还剩什么）
    写进子模块的 remaining_estimate —— 这样 remaining 由 planner/split 权威传递，子模块出口判定
    读自己的 remaining_estimate 递归，不再靠 executor 自报（remaining 不是 executor 的活）。
    """
    parent = ctx.modules[mid]
    child_id = str(child.get("id"))
    name = str(child.get("name") or child_id)
    objective = str(child.get("objective") or "")
    deliverables = [str(x) for x in (child.get("deliverables") or [])]
    files = [str(x) for x in (child.get("files") or [])]
    deps = _resolve_child_deps(ctx, mid, dep_map, child_id)
    module_dict = {
        "id": child_id,
        "name": name,
        "layer": int(parent.layer) + 1,
        "objective": objective,
        "dependencies": list(deps),
        "deliverables": list(deliverables),
        "acceptance": list(deliverables) or [f"{child_id} 验收：按 contract.yaml 产出 src 产物"],
        "files": list(files),
        "interfaces": [],
        "boundaries": [f"{child_id} 继承父模块 {mid} 边界，不跨界"],
    }
    # remaining_after → 子模块 remaining_estimate（remaining 递归传递，出口判定据此 split/final/done）
    ra = dict(remaining_after or {})
    if ra.get("scope") or ra.get("estimate_lines") is not None:
        module_dict["remaining_estimate"] = {
            "scope": str(ra.get("scope") or "剩余部分"),
            "estimate_lines": ra.get("estimate_lines"),
        }
    return child_id, module_dict


def scaffold_children(ctx: TaskContext, mid: str, split_json: Mapping[str, Any]) -> List[str]:
    """D3（v2）：落地 next_block 单块子模块标准目录（modules/ 平级）+ 注册 ModuleSpec。

    每个子模块生成：任务书-{id}.yaml / REVIEW.md / contract.yaml / 交付说明.md / src/ / test/，
    与 fw-scaffold 标准结构对齐（logs/ tmp/ 豁免区标记一并生成）。贪心单块：一次只拆一块。
    """
    parent = ctx.modules[mid]
    modules_root = parent.dir.parent
    dep_map = split_json.get("dependency_map") or {}
    nb = split_json.get("next_block") or {}
    all_modules = [m for m in (ctx.effective.get("modules") or []) if isinstance(m, dict)]

    if not nb:
        raise SplitJSONError("next_block 缺失（v2 协议：一次只拆下一块）")
    child_id, module_dict = _build_child_module_dict(ctx, mid, nb, dep_map,
                                                     split_json.get("remaining_after"))
    if child_id in ctx.modules:
        raise SplitJSONError(f"子模块 id 冲突（已存在模块）: {child_id}")
    child_dir = modules_root / f"{child_id}-{_sanitize_name(str(nb.get('name') or child_id))}"
    spec = ModuleSpec(
        id=child_id,
        name=str(nb.get("name") or child_id),
        layer=int(parent.layer) + 1,
        objective=str(nb.get("objective") or ""),
        dependencies=list(module_dict["dependencies"]),
        dir=child_dir,
        review_path=child_dir / "REVIEW.md",
        contract_path=child_dir / "contract.yaml",
        book_path=child_dir / f"任务书-{child_id}.yaml",
        delivery_path=child_dir / "交付说明.md",
    )
    # 标准目录 + 文件（fs 原子写）
    for sub in ("src", "test"):
        (child_dir / sub).mkdir(parents=True, exist_ok=True)
        (child_dir / sub / ".gitkeep").write_text("", encoding="utf-8")
    for sub in ("logs", "tmp"):
        (child_dir / sub).mkdir(parents=True, exist_ok=True)
        atomic_write_text(child_dir / sub / ".auditor-ignore",
                          f".auditor-ignore —— {sub}/（auditor 豁免区标记，split 派生）\n")
    atomic_write_text(spec.review_path,
                      _child_review(child_id, spec.name, spec.objective,
                                    spec.dependencies,
                                    module_dict.get("deliverables") or [], mid))
    atomic_write_text(spec.contract_path, _child_contract(
        child_id, spec.name,
        data_contract=(ctx.effective.get("task") or {}).get("data_contract") or None))
    atomic_write_text(spec.book_path, _derive_child_book(ctx.effective, module_dict, all_modules))
    atomic_write_text(spec.delivery_path, _child_delivery(child_id, spec.name, mid))
    ctx.modules[child_id] = spec
    return [child_id]


def generate_shared_context(ctx: TaskContext, mid: str,
                            split_json: Mapping[str, Any],
                            aout: Any = None) -> Path:
    """D4：生成父模块目录 SHARED_CONTEXT.md（设计文档第七节）。

    内容：接口摘要（程序 AST 提取，0 token）+ auditor 工具结果（codegraph/semgrep）
    + 已完成文件列表 / 已通过的功能点 / 未完成 TODO / 父模块 REVIEW 摘要
    + context_from_parent。子模块 executor 强制先读——不需要逐个读父模块源文件。
    """
    spec = ctx.modules[mid]
    files = _scan_src_files(spec)
    review_done: List[str] = []
    review_todo: List[str] = []
    review_raw = ""
    if spec.review_path.is_file():
        from .review import read_review
        doc = read_review(spec.review_path)
        review_raw = doc.raw
        review_done = [_clean_done(ln) for ln in doc.list_done() if "（占位）" not in ln]
        review_todo = [_clean_todo(ln) for ln in doc.list_todo() if "（占位）" not in ln]

    nb = split_json.get("next_block") or {}
    child_ids = [str(nb.get("id"))] if nb.get("id") else []
    lines = [
        f"# SHARED_CONTEXT.md —— {mid}（父模块拆分共享上下文）",
        "",
        f"> 父模块 {mid} 已被拆分为子模块。子模块 executor 开工必须**先读本文件**，",
        "> 不重做已完成部分。**接口摘要已由程序提取（0 token），不需要逐个读父模块源文件。**",
        "> 父模块标记 split（容器，不再执行）；子模块全部 done 后",
        "> 父模块自动聚合 done。由 runner split 时自动生成。",
        "",
        "## 父模块元信息",
        f"parent_module: {mid}",
        f"parent_objective: {spec.objective}",
        f"children: {', '.join(child_ids) if child_ids else '(空)'}",
        f"context_from_parent: {split_json.get('context_from_parent', '')}",
        "",
        "## 接口摘要（程序 AST 提取，子 executor 只读本节即可对接）",
    ]
    iface = _extract_interface_summary(spec)
    lines += iface if iface else ["- （无公开接口或解析失败）"]
    lines += [
        "",
        "## 审计工具结果（auditor 跑的 codegraph/semgrep，子 executor 直接复用）",
    ]
    tool_lines = _extract_audit_tool_results(aout)
    lines += tool_lines if tool_lines else ["- （无审计工具结果）"]
    lines += [
        "",
        "## 已完成文件列表（绝对路径，非必要不读）",
    ]
    lines += [f"- {f}" for f in files] if files else ["- （无）"]
    lines += ["", "## 已通过的功能点"]
    lines += [f"- {d}" for d in review_done] if review_done else ["- （无）"]
    lines += ["", "## 未完成 TODO"]
    lines += [f"- [ ] {t}" for t in review_todo] if review_todo else ["- （无）"]
    lines += ["", "## 父模块 REVIEW 摘要", ""]
    lines += review_raw.splitlines() if review_raw.strip() else ["- （无）"]

    path = spec.dir / SHARED_CONTEXT_NAME
    content = "\n".join(lines) + "\n"
    atomic_write_text(path, content)
    # 复制到所有子模块目录（透传接口摘要，子 executor 不需要逐个读父模块源文件）
    for cid in child_ids:
        if cid in ctx.modules:
            child_path = ctx.modules[cid].dir / SHARED_CONTEXT_NAME
            atomic_write_text(child_path, content)
    # 生成全景板（程序侧结构化任务状态，所有角色可引用）
    _write_panorama(ctx, mid, child_ids)
    return path


def _write_panorama(ctx: TaskContext, mid: str, child_ids: List[str]) -> None:
    """生成全景板 PANORAMA.json（程序侧结构化任务状态，所有角色可引用）。

    写到父模块目录 + 子模块目录，包含总目标、各模块树枝目标、状态、接口摘要。
    这是一次性定义、全链路引用的权威视图。
    """
    import json as _json
    modules_view = []
    for m_id, m_spec in ctx.modules.items():
        iface = _extract_interface_summary(m_spec) if (m_spec.dir / "src").is_dir() else []
        modules_view.append({
            "id": m_id,
            "name": m_spec.name,
            "layer": m_spec.layer,
            "objective": m_spec.objective,
            "dependencies": m_spec.dependencies,
            "interfaces": iface,
        })
    pano = {
        "task_goal": (ctx.effective.get("task") or {}).get("goal") or "",
        "will_not_have": (ctx.effective.get("task") or {}).get("prediction_baseline", {}).get("will_not_have") or [],
        "parent_module": mid,
        "children": child_ids,
        "modules": modules_view,
        "generated_by": "split.generate_shared_context",
    }
    payload = _json.dumps(pano, ensure_ascii=False, indent=2) + "\n"
    # 写到父模块目录
    parent_pano = ctx.modules[mid].dir / "PANORAMA.json"
    atomic_write_text(parent_pano, payload)
    # 透传到子模块
    for cid in child_ids:
        if cid in ctx.modules:
            child_pano = ctx.modules[cid].dir / "PANORAMA.json"
            atomic_write_text(child_pano, payload)


def _extract_audit_tool_results(aout: Any) -> List[str]:
    """从 auditor outcome 提取 codegraph/semgrep 等工具结果，供子 executor 复用。

    返回 Markdown 列表。aout 为 None 时返回空。
    """
    if aout is None:
        return []
    evidence = getattr(aout, "evidence", None) or []
    if isinstance(aout, dict):
        evidence = aout.get("evidence") or []
    if not evidence:
        return []
    lines: List[str] = []
    for e in evidence:
        e_str = str(e)[:500]
        if any(kw in e_str.lower() for kw in ("codegraph", "越界", "影响面", "semgrep", "扫描", "import", "跨模块")):
            lines.append(f"- {e_str}")
    return lines


def _extract_interface_summary(spec: ModuleSpec) -> List[str]:
    """程序侧提取父模块 src/ 下的公开接口摘要（0 token，Python AST）。

    返回 Markdown 列表，每条 = 函数/类签名 + 用途（从 docstring 首行）。
    只提取公开符号（不含 _ 前缀），跳过 __init__/__main__ 等特殊模块。
    解析失败静默返回空列表（不阻塞 split 流程）。
    """
    import ast as _ast
    src_dir = spec.dir / "src"
    if not src_dir.is_dir():
        return []
    lines: List[str] = []
    py_files = sorted(
        p for p in src_dir.rglob("*.py")
        if p.is_file() and not p.name.startswith("_") and p.name != "__main__.py"
    )
    for fpath in py_files:
        try:
            tree = _ast.parse(fpath.read_text(encoding="utf-8"))
        except (SyntaxError, OSError, UnicodeDecodeError):
            continue
        rel = fpath.relative_to(spec.dir)
        for node in _ast.iter_child_nodes(tree):
            if isinstance(node, _ast.FunctionDef):
                if node.name.startswith("_"):
                    continue
                sig = _render_func_sig(node)
                doc = _ast.get_docstring(node)
                if doc:
                    doc_line = doc.split("\n")[0].strip()
                    lines.append(f"- `{rel}` → `{sig}` — {doc_line}")
                else:
                    lines.append(f"- `{rel}` → `{sig}`")
            elif isinstance(node, _ast.ClassDef):
                if node.name.startswith("_"):
                    continue
                doc = _ast.get_docstring(node)
                if doc:
                    doc_line = doc.split("\n")[0].strip()
                    lines.append(f"- `{rel}` → `class {node.name}` — {doc_line}")
                else:
                    lines.append(f"- `{rel}` → `class {node.name}`")
                # 类的公开方法
                for body_node in node.body:
                    if isinstance(body_node, _ast.FunctionDef) and not body_node.name.startswith("_"):
                        sig = _render_func_sig(body_node)
                        mdoc = _ast.get_docstring(body_node)
                        if mdoc:
                            mdoc_line = mdoc.split("\n")[0].strip()
                            lines.append(f"  - `.{body_node.name}{sig}` — {mdoc_line}")
                        else:
                            lines.append(f"  - `.{body_node.name}{sig}`")
    return lines


def _render_func_sig(func: Any) -> str:
    """渲染函数签名（不含函数名，只渲染参数）。"""
    import ast as _ast
    args = func.args
    parts: List[str] = []
    # 位置参数
    for a in args.args:
        parts.append(a.arg)
    # 默认值参数
    defaults = args.defaults
    if defaults:
        offset = len(args.args) - len(defaults)
        for i, d in enumerate(defaults):
            idx = offset + i
            if idx < len(parts):
                val = _ast.unparse(d) if hasattr(_ast, "unparse") else "..."
                parts[idx] += f"={val}"
    # *args
    if args.vararg:
        parts.append(f"*{args.vararg.arg}")
    # 关键字参数
    for a in args.kwonlyargs:
        parts.append(f"{a.arg}=...")
    # **kwargs
    if args.kwarg:
        parts.append(f"**{args.kwarg.arg}")
    # 返回类型
    ret = ""
    if func.returns:
        try:
            ret = f" -> {_ast.unparse(func.returns)}" if hasattr(_ast, "unparse") else ""
        except Exception:
            ret = ""
    return f"({', '.join(parts)}){ret}"


def insert_children_into_order(ctx: TaskContext, mid: str, child_ids: Sequence[str],
                               dep_map: Mapping[str, Any]) -> None:
    """D5：子模块插入 module_order（父模块之后）+ 依赖图更新。

    依赖规则：子模块继承父模块上游依赖 + dependency_map；剔除父模块自身（父是容器，
    下游等父聚合 done，子模块不得依赖父，防环）。幂等：重复调用不产生重复条目。
    """
    if mid not in ctx.module_order:
        raise SplitJSONError(f"父模块 {mid} 不在 module_order 中，无法插入子模块")
    known = set(ctx.modules.keys())
    children = [cid for cid in child_ids if cid in ctx.modules]
    missing = [cid for cid in child_ids if cid not in children]
    if missing:
        raise SplitJSONError(f"子模块未注册（缺 ModuleSpec）: {', '.join(missing)}")

    for cid in children:
        if cid in ctx.module_order:
            ctx.module_order.remove(cid)
    idx = ctx.module_order.index(mid) + 1
    for cid in children:
        deps = _resolve_child_deps(ctx, mid, dep_map, cid)
        ctx.dependencies[cid] = deps
        spec = ctx.modules[cid]
        spec.dependencies = list(deps)
        spec.layer = ctx.modules[mid].layer + 1
        ctx.module_order.insert(idx, cid)
        idx += 1
