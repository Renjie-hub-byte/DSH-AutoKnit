"""fw-scaffold 模板：REVIEW.md / contract.yaml / 交付说明.md / shared 与豁免区标记。

- REVIEW.md        : 模块验收闭环（v0.4）：executor 开工先读、auditor 打回写回、换 executor 交接。
                      含机器可解析键值行（key: value），供 runner/auditor 程序读取。
- contract.yaml    : 模块接口契约（千问 10.2）：input/output 占位由 executor 填写，
                      read_api 由脚手架从总任务书 interfaces 预填（与总任务书语义一致）。
- 交付说明.md       : executor 交付报告模板（改了什么/测了什么/风险）。
- shared/          : 只读共享区标记（README + .readonly）；不属于 auditor 豁免区。
- logs/ + tmp/     : 豁免区标记 .auditor-ignore（auditor 检查忽略）。
"""
from __future__ import annotations

import datetime as _dt
from .io_utils import SCAFFOLD_VERSION
from typing import Any, Dict, List, Mapping, Optional, Sequence

MODULE_STATUS_OPTIONS = "pending | working | needs_review | blocked | done"
ROOT_CAUSE_OPTIONS = "self | upstream | contract | (空=未定)"


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def review_md(module: Mapping[str, Any], task_name: str) -> str:
    """REVIEW.md 模板（机器可解析键值行 + 手写小节）。"""
    mid = module.get("id", "?")
    name = module.get("name", "?")
    objective = module.get("objective", "")
    deps = list(module.get("dependencies") or [])
    return f"""# REVIEW —— {mid} {name}（模块验收闭环）

> 用途：executor 开工先读本文件 → 列 todo → 干活 → 自测外部验收；auditor 打回报判定与根因
> 写回本文件；换 executor 时本文件是交接三件套之一。保持下方机器可解析键值行格式（key: value）。
> 模板由 fw-scaffold 生成（scaffold_version 见任务根 .scaffold-version）；不删除任何键。

## 模块元信息
id: {mid}
name: {name}
task: {task_name}
objective: {objective}
dependencies: {", ".join(deps) if deps else "(无)"}
status: pending            # 合法值: {MODULE_STATUS_OPTIONS}
executor_round: 0
auditor_round: 0

## 待办（todo，executor 开工第一件事：把本模块验收清单拆成可执行 todo）
- [ ] 通读任务书-{mid}.yaml 与 contract.yaml、REVIEW.md
- [ ] （占位）按验收清单逐条列出任务

## 已做（done，按事件 seq 或时间记录，可追溯）
- （占位）

## 问题与根因（失败分类器）
root:                # 合法值: {ROOT_CAUSE_OPTIONS}
detail:

## 置信度
confidence: 0.0      # auditor 判定置信度 0-1

## 交接说明（换 executor 时读：问题 / 现象 / 已试办法；新 executor 据此续作）
现象:
已试办法:

## 外部验收自测（executor 交付前逐条对照任务书-{mid}.yaml 的 acceptance）
- （占位，逐条标注 通过/不通过/证据）
"""


def contract_yaml(module: Mapping[str, Any], task_name: str,
                  data_contract: Optional[Mapping[str, Any]] = None) -> str:
    """contract.yaml 模板：input/output 占位，read_api 从总任务书 interfaces 预填（含 method 数据字段）。

    data_contract 非空时追加 shared_data 段（与任务根 contracts/data.yaml 同一份，全模块一致）。
    """
    import json as _json
    mid = module.get("id", "?")
    name = module.get("name", "?")
    interfaces = list(module.get("interfaces") or [])
    lines = [
        f"# modules/{mid}-{name}/contract.yaml —— 模块接口契约（fw-integrate 运行时校验用）",
        "# 模板由 fw-scaffold 从总任务书派生生成：read_api 预填总任务书声明的接口（path/method/note）；",
        "# input/output 是执行期涌现字段，由 executor 开工时填写（规划期禁止硬定字段）。",
        f"module: {mid}",
        f"name: {name}",
        f"task: {task_name}",
        "input:                 # <该模块需要的输入 schema>（占位，executor 填写）",
        "  from: []             # 上游模块产出 / shared/ 文件 / 事件载荷",
        "  describe:            # 一句话描述输入格式（占位）",
        "output:                # <该模块产出的输出 schema>（占位，executor 填写）",
        "  artifacts: []        # 产物相对路径列表（如 src/data/orders.json）",
        "  describe:            # 一句话描述输出格式（占位）",
        "read_api:              # 本模块只读暴露 API（跨模块只能通过这些读；预填自总任务书）",
    ]
    if interfaces:
        for it in interfaces:
            method = it.get("method")
            method_repr = (
                _json.dumps(list(method)) if isinstance(method, list) else f'"{method}"'
            )
            lines.append(f"  - path: {it.get('path', '')}")
            lines.append(f"    method: {method_repr}")
            if it.get("direction"):
                lines.append(f"    direction: {it['direction']}")
            if it.get("note"):
                lines.append(f"    note: {it['note']}")
            ds = it.get("data_shape")
            if ds:
                import yaml as _yaml
                ds_yaml = _yaml.safe_dump(ds, allow_unicode=True, sort_keys=False,
                                          default_flow_style=False, indent=2).rstrip("\n")
                lines.append("    data_shape:")
                lines.extend("      " + ln for ln in ds_yaml.splitlines())
    else:
        lines.append("  []                    # 无接口声明")
    if data_contract:
        import yaml as _yaml
        lines.append("")
        lines.append("shared_data:           # 跨模块共享数据契约（全模块同一份；任务根 contracts/data.yaml 是唯一事实源）")
        dc_yaml = _yaml.safe_dump(data_contract, allow_unicode=True, sort_keys=False,
                                  default_flow_style=False, indent=2).rstrip("\n")
        lines.extend("  " + ln for ln in dc_yaml.splitlines())
    lines.append("")
    return "\n".join(lines)


def json_dumps_list(v: Sequence[str]) -> str:
    import json
    return json.dumps(list(v), ensure_ascii=False)


def delivery_md(module: Mapping[str, Any], task_name: str) -> str:
    mid = module.get("id", "?")
    name = module.get("name", "?")
    return f"""# 交付说明 —— {mid} {name}

> executor 交付报告（改了什么 / 测了什么 / 风险）。由 fw-scaffold 初始化模板，executor 按节填写。
> 任务：{task_name} ｜ 生成时间：{_now()}

## 改动内容（改了什么）
- （占位）

## 测试结果（测了什么，含命令与输出摘要）
- （占位）

## 外部验收自测（对照任务书-{mid}.yaml 的 acceptance 逐条自测）
- （占位）

## 已知风险 / 边界（如实标注限制、未覆盖场景、妥协点）
- （占位）

## 交接备注（如需换 executor / 回人拍板时留给下游的信息）
- （占位）
"""


def shared_readme() -> str:
    return """# shared/ —— 只读共享区（跨模块只读共享）

规则（目录规范 v2 / 执行配置 9.6）：
- **只读**：任何模块/角色不得直接写 shared/。需要改动共享文件 → 复制 → 改副本 → 合并 → 经集成验收。
- **不属于 auditor 豁免区**：shared/ 的变更会触发审计与集成验收（与 logs/、tmp/ 豁免区相反）。
- 程序标记：本目录含 `.readonly` 文件，机器可据此识别只读性质（sandbox 可强制 read-only）。
- 典型用途：接口契约板、跨模块共享数据、只读参考资料。

由 fw-scaffold 生成（目录规范 v2）。写入需走 复制→改→合并 流程。
"""


def cognition_readme() -> str:
    return """# 认知/ —— 规划认知区

planner 开局调研产出（限预算）与滚动规划纪要放这里：竞品/技术栈/约束调研、任务书拆解依据、
评审吸收记录。读者：executor/auditor 理解任务上下文；写入者：planner（只拆不写的例外是认知区）。

由 fw-scaffold 生成（目录规范 v2）。
"""


def auditor_ignore(kind: str) -> str:
    return f""".auditor-ignore —— {kind}（auditor 豁免区标记）

本目录属于执行期豁免区：auditor 过程审计与结果核对**跳过**此处内容（不属于交付物校验范围）。
- logs/: 模块执行期日志（executor 事件 + auditor 报告）
- tmp/:  临时文件 / 中间产物（可随时清空，不视为交付物）

由 fw-scaffold 生成（目录规范 v2 / 执行配置 9.6）。runner/auditor 读到此标记即忽略本目录。
"""


def gitkeep() -> str:
    return ""  # 空占位，保证空目录进 git


def root_task_yaml_header(task_name: str) -> str:
    return f"""# task.yaml —— 任务书（effective 版本，默认值已补全）
# 由 fw-scaffold 从输入 task.yaml 校验通过后派生写入（fw-protocol validate_file().effective）。
# 任务：{task_name} ｜ 目录规范 v2 ｜ 下游（runner/integrate）以本文件为唯一事实源。
"""


def skeleton_md(effective: Mapping[str, Any]) -> str:
    """skeleton.md：骨架说明（每层 1-2 句整体视图，横向对齐防跑偏），由任务书派生。"""
    task = effective.get("task") or {}
    tname = task.get("name", "?")
    modules = [m for m in (effective.get("modules") or []) if isinstance(m, dict)]
    layers: Dict[int, List[str]] = {}
    for m in modules:
        layers.setdefault(int(m.get("layer", 1)), []).append(m)
    integ = effective.get("integration") or {}
    lines = [
        f"# 骨架说明 —— {tname}",
        "",
        f"> 由 fw-scaffold 从 task.yaml 派生（目录规范 v2）。每层 1-2 句整体视图，横向对齐防跑偏。",
        "",
        "## 模块总览（按 layer）",
    ]
    for layer in sorted(layers):
        lines.append(f"### layer {layer}")
        for m in sorted(layers[layer], key=lambda x: x.get("id", "")):
            deps = m.get("dependencies") or []
            dep_s = ", ".join(deps) if deps else "无"
            lines.append(f"- {m.get('id')}-{m.get('name')}：{m.get('objective')}（依赖: {dep_s}）")
    edges = [(m.get("id"), m.get("dependencies") or []) for m in modules if m.get("id")]
    lines.append("")
    lines.append("## 依赖链（拓扑视角）")
    if edges:
        for mid, deps in edges:
            dep_s = ", ".join(deps) if deps else "（根模块）"
            lines.append(f"- {mid} ← {dep_s}")
    else:
        lines.append("- （无依赖声明）")
    lines.append("")
    lines.append("## 集成配置")
    lines.append(f"- contract_file: {integ.get('contract_file', 'contracts/api.yaml')}")
    checks = integ.get("check") or {}
    for k, v in checks.items() if isinstance(checks, dict) else []:
        lines.append(f"- check.{k}: {v}")
    lines.append("")
    lines.append("## 已知边界与全局约束")
    for constraint in task.get("prediction_baseline", {}).get("will_not_have", []) if isinstance(task.get("prediction_baseline"), dict) else []:
        lines.append(f"- 不会做：{constraint}")
    lines.append("")
    return "\n".join(lines)


def data_contract_yaml(effective: Mapping[str, Any], task_name: str) -> str:
    """contracts/data.yaml：数据契约区 —— 跨模块共享存储/枚举/布局（fw 全链路唯一事实源）。

    仅当 task.data_contract 非空时由 scaffold 生成；executor/auditor 的 EXEC_TASK 注入
    与各模块 contract.yaml 的 shared_data 段都以此文件为同一内容（字节级一致）。
    """
    import yaml as _yaml
    task = effective.get("task") or {}
    dc = task.get("data_contract") or {}
    lines = [
        "# contracts/data.yaml —— 数据契约区：跨模块共享存储/枚举/布局（全链路唯一事实源）",
        "# 由 fw-scaffold 从总任务书 task.data_contract 派生；所有模块 contract.yaml 与 EXEC_TASK",
        "# 注入的共享数据契约段与此文件字节级一致。禁止任何模块自定义表名/路径/格式——以此为准。",
        f"task: {task_name}",
        "data_contract:",
    ]
    dc_yaml = _yaml.safe_dump(dc, allow_unicode=True, sort_keys=False,
                              default_flow_style=False, indent=2).rstrip("\n")
    lines.extend("  " + ln for ln in dc_yaml.splitlines())
    lines.append("")
    return "\n".join(lines)


def contract_api_yaml(effective: Mapping[str, Any]) -> str:
    """contracts/api.yaml：契约区 —— 所有模块接口协议汇总（fw-integrate 运行时校验基线）。"""
    task = effective.get("task") or {}
    tname = task.get("name", "?")
    modules = [m for m in (effective.get("modules") or []) if isinstance(m, dict)]
    lines = [
        "# contracts/api.yaml —— 契约区：所有模块接口协议汇总（集成验收运行时校验基线）",
        "# 由 fw-scaffold 从总任务书 interfaces 派生；模块内部细化（input/output 字段）在",
        "# 各模块 contract.yaml，跨模块数据依赖检查由 fw-integrate 执行。",
        f"task: {tname}",
        "version: 1",
        "api:",
    ]
    count = 0
    for m in sorted(modules, key=lambda x: x.get("id", "")):
        for it in (m.get("interfaces") or []):
            if not isinstance(it, dict):
                continue
            method = it.get("method")
            method_repr = (
                json_dumps_list(method) if isinstance(method, list) else f'"{method}"'
            )
            note = it.get("note")
            note_repr = f"  # {note}" if note else ""
            lines.append(f"  - module: {m.get('id')}")
            lines.append(f"    path: {it.get('path', '')}")
            lines.append(f"    method: {method_repr}{note_repr}")
            count += 1
    if count == 0:
        lines.append("  []            # 无接口声明")
    lines.append("")
    return "\n".join(lines)


def dispatch_init(task_name: str, generated_at: str, dirs: List[str]) -> str:
    import json
    line = {
        "ts": generated_at,
        "event": "scaffold",
        "module": None,
        "action": "created",
        "detail": {"task": task_name, "directories": dirs, "scaffold_version": SCAFFOLD_VERSION},
    }
    return json.dumps(line, ensure_ascii=False) + "\n"


def integration_init(task_name: str, generated_at: str) -> str:
    import json
    line = {
        "ts": generated_at,
        "event": "integration.init",
        "detail": {"task": task_name, "note": "scaffold 初始化；模块合流与基线对照由 fw-integrate 追加"},
    }
    return json.dumps(line, ensure_ascii=False) + "\n"


def snapshot_init(task_name: str, modules: Sequence[Mapping[str, Any]], generated_at: str) -> str:
    """总日志/快照.json 初始状态（scaffold 只负责初始化；checkpoint 更新归 fw-runner）。"""
    import json
    mod_ids = [m.get("id") for m in modules if m.get("id")]
    deps: Dict[str, List[str]] = {
        m.get("id"): list(m.get("dependencies") or [])
        for m in modules if m.get("id")
    }
    doc = {
        "schema_version": 2,
        "task": task_name,
        "generated_at": generated_at,
        "scaffold_version": SCAFFOLD_VERSION,
        "status": "scaffolded",
        "modules": {mid: "pending" for mid in mod_ids},
        "dependencies": deps,
        "failure_counts": {mid: 0 for mid in mod_ids},
        "budget_used_tokens": 0,
        "completed_count": 0,
        "note": "初始快照由 fw-scaffold 生成；执行期 checkpoint 更新归 fw-runner",
    }
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"

