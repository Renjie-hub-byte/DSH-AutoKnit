"""fw-integrate 测试助手：真实 v2 目录树（fw-scaffold 生成）+ 真实 runner 产出 + 契约填写。

- build_task        : 写 task.yaml → fw-scaffold.generate → 返回任务根（真实脚手架）
- run_runner_inline : 用 fw-runner.run + inline 驱动真实跑一遍；executor 按契约填
                      contract.yaml / 落真实产物 / 交付说明，快照与 integration.jsonl 为
                      真实 runner 产物（strongest evidence：集成消费的是真实执行结果）
- make_full_delivery: 手工把一棵脚手架树改造成“全交付”形态（不跑 runner，快照手工写）
- 篡改助手           : tamper_read_api_add_method / tamper_missing_artifact / 等
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

_FW1 = Path(__file__).resolve().parent.parent.parent
for _d in ("fw-integrate", "fw-scaffold", "fw-protocol", "fw-runner", "fw-budget"):
    _p = str((_FW1 / _d).resolve())
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------- 任务书 + 脚手架

DEFAULT_BASELINE = {
    "will_have": [
        "订单数据落盘为 JSON（src/data/orders.json 结构按契约）",
        "清洗模块产出标准化订单记录（含字段校验）",
        "报表模块输出按日聚合的订单统计 CSV",
    ],
    "will_not_have": [
        "不做实时流式处理（本任务是批处理）",
        "不做支付与风控联动",
    ],
}


def write_task_doc(tmp_path: Path, name: str, modules, runtime=None, budget=None,
                   baseline=None, integration_checks=None) -> Path:
    doc = {
        "task": {
            "name": name, "source_prd": "prd/x.md", "owner": "tester",
            "created": "2026-08-21", "grade": "B",
            "prediction_baseline": baseline or DEFAULT_BASELINE,
        },
        "budget": budget or {"max_tokens": 500000, "warn_at": 0.7, "stop_at": 1.0,
                             "per_module_max_tokens": 200000},
        "runtime": runtime or {"max_parallel": 2, "executor_max_rounds": 5,
                               "retry_before_switch": 2, "max_executor_switches": 1,
                               "end_gate": "auto"},
        "modules": modules,
        "integration": {
            "contract_file": "contracts/api.yaml",
            "check": integration_checks or {
                "dependency_cycle": True, "interface_duplicate": True,
                "acceptance_conflict": True, "prediction_baseline": True,
                "cross_module_data_dependency": True,
            },
        },
    }
    p = tmp_path / "task.yaml"
    p.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return p


def module(id_: str, name: str, deps=None, layer: int = 1, objective: str = "目标",
           interfaces=None) -> dict:
    return {
        "id": id_, "name": name, "layer": layer, "objective": objective,
        "dependencies": deps or [],
        "interfaces": interfaces or [{"path": f"/api/{id_}/*", "method": ["GET"],
                                      "note": f"{id_} 接口"}],
        "acceptance": [f"{id_} 验收：按 contract.yaml 产出 src 产物"],
        "boundaries": [f"{id_} 不跨界"],
    }


def build_task(tmp_path: Path, name: str, modules, runtime=None, budget=None,
               baseline=None) -> Path:
    """写 task.yaml → fw-scaffold 生成 v2 目录树 → 返回任务根。"""
    from fw_scaffold.scaffold import generate
    yaml_path = write_task_doc(tmp_path, name, modules, runtime=runtime, budget=budget,
                               baseline=baseline)
    res = generate(yaml_path, output_dir=tmp_path)
    return Path(res.root)


def module_dir(root: Path, mid: str) -> Path:
    d = root / "modules"
    for entry in sorted(d.iterdir()):
        if entry.is_dir() and entry.name.startswith(mid + "-"):
            return entry
    raise FileNotFoundError(f"找不到模块目录 {mid} in {d}")


# ---------------------------------------------------------------- contract.yaml 填写

def fill_contract(module_dir: Path, input_from: Optional[List[str]], artifacts: List[str],
                  describe: str, read_api_add=None, output_describe: Optional[str] = None) -> None:
    """改写 module/contract.yaml 的模板占位：from / artifacts / describe（输入与输出分别定位）。

    模板两处 describe 行带不同注释（输入/输出），按注释区分替换，避免错位。
    read_api_add: dict {path, method, note} → 在 read_api 列表末尾按 YAML 规整缩进追加一条。
    """
    p = module_dir / "contract.yaml"
    text = p.read_text(encoding="utf-8")
    lines = text.splitlines()
    out: List[str] = []
    for ln in lines:
        stripped = ln.strip()
        if stripped.startswith("from: []"):
            inside = "  from: [%s]" % ", ".join(input_from) if input_from else "  from: []"
            out.append(inside)
        elif stripped.startswith("artifacts: []"):
            inside = "  artifacts: [%s]" % ", ".join(artifacts) if artifacts else "  artifacts: []"
            out.append(inside)
        elif stripped.startswith("describe:") and "输入格式" in ln:
            out.append("  describe: %s" % describe)
        elif stripped.startswith("describe:") and "输出格式" in ln:
            out.append("  describe: %s" % (output_describe or describe))
        else:
            out.append(ln)
    text = "\n".join(out)
    if read_api_add:
        method = json.dumps(read_api_add["method"])
        block = ("  - path: %s\n"
                 "    method: %s\n"
                 "    note: %s\n" % (read_api_add["path"], method,
                                      read_api_add.get("note", "")))
        text = text.rstrip("\n") + "\n" + block
    p.write_text(text, encoding="utf-8")


def append_delivery(module_dir: Path, extra: str) -> None:
    (module_dir / "交付说明.md").write_text(
        (module_dir / "交付说明.md").read_text(encoding="utf-8") + "\n" + extra,
        encoding="utf-8")


# ---------------------------------------------------------------- 真实 runner 执行（inline 驱动，产物按模块映射）

PRODUCER_ARTIFACTS = {
    "m01": ["src/data/orders.json"],
    "m02": ["src/data/cleaned_orders.json"],
    "m03": ["src/data/daily_orders.csv"],
}
DELIVERY_EVIDENCE = {
    "m01": "## 交付摘要\n- 订单数据已落盘为 JSON（src/data/orders.json），结构符合契约。\n",
    "m02": "## 交付摘要\n- 清洗模块产出标准化订单记录（含字段校验），见 cleaned_orders.json。\n",
    "m03": "## 交付摘要\n- 报表模块输出按日聚合的订单统计 CSV（daily_orders.csv）。\n",
}
ARTIFACT_CONTENT = {
    "src/data/orders.json": '[{"order_id": "A1", "amount": 12.5}]',
    "src/data/cleaned_orders.json": '[{"order_id": "A1", "amount": 12.5, "valid": true}]',
    "src/data/daily_orders.csv": "date,count,total\n2026-08-21,1,12.5\n",
}


def conforming_executor(root: Path, artifacts_map: Optional[Dict[str, List[str]]] = None,
                        delivery_map: Optional[Dict[str, str]] = None):
    """返回 fw-runner InlineAgentDriver 的 executor 函数：按模块落真实产物 + 填契约 + 交付说明。

    依赖链映射（input.from 按任务书模块 id 生成）：m02←m01，m03←m02（按 deps 推导）。
    """
    arts = artifacts_map or PRODUCER_ARTIFACTS
    dels = delivery_map or DELIVERY_EVIDENCE
    from fw_runner.drivers import InlineAgentDriver
    from fw_runner.model import DriverOutcome
    from fw_runner.review import append_done

    def fn(ctx):
        mid = ctx.module.id
        mdir = module_dir(root, mid)
        # REVIEW 已做（真实文件效应）
        append_done(mdir / "REVIEW.md", f"{mid} 交付轮 {ctx.round_no}（{ctx.executor_id}）执行完成")
        # 产物
        for rel in arts.get(mid, []):
            target = mdir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(ARTIFACT_CONTENT.get(rel, "{}"), encoding="utf-8")
        # contract.yaml：input.from（按任务书 deps）+ output.artifacts
        deps = ctx.module.dependencies or []
        contract = mdir / "contract.yaml"
        if contract.is_file():
            fill_contract(mdir, list(deps), list(arts.get(mid, [])),
                          f"{mid} 交付产物见 output.artifacts")
        # 交付说明（基线证据）
        if mid in dels:
            append_delivery(mdir, dels[mid])
        return DriverOutcome(status="ok", substance=True, tokens=0)

    return InlineAgentDriver(fn)


def conforming_auditor():
    """返回 fw-runner InlineAgentDriver 的 auditor 函数：有产物与已做 → pass。"""
    from fw_runner.drivers import InlineAgentDriver
    from fw_runner.model import DriverOutcome
    from fw_runner.review import read_review

    def fn(ctx):
        try:
            doc = read_review(ctx.module.review_path)
            done = [ln for ln in doc.list_done() if "（占位）" not in ln]
        except Exception:
            done = []
        arts = [p for p in (ctx.module.dir / "src").rglob("*") if p.is_file()]
        if done and arts:
            return DriverOutcome(status="ok", verdict="pass", root="", confidence=0.9,
                                 reason=f"{ctx.module.id} 交付验收通过", tokens=0)
        return DriverOutcome(status="ok", verdict="block", root="self", confidence=0.5,
                             reason="缺产物/已做", blocker="缺 src 产物", tokens=0)

    return InlineAgentDriver(fn)


def run_runner_inline(root: Path, executor_driver, auditor_driver=None):
    """真实跑 fw-runner（inline 驱动）。返回 RunnerResult（快照/事件流/集成日志均为真实产物）。"""
    from fw_runner.runner import run as runner_run
    auditor = auditor_driver or conforming_auditor()
    return runner_run(root, executor_driver=executor_driver, auditor_driver=auditor,
                      integration_hook=None)


# ---------------------------------------------------------------- 手工快照（不跑 runner 的快速路径）

def make_complete_snapshot(root: Path, run_id: str = "run-test-integrate-0001",
                           modules=None, deps=None) -> None:
    """写一份符合快照 schema v3 的 complete 快照（仅测试保真；真实路径走 run_runner_inline）。"""
    mids = [d.name.split("-", 1)[0] for d in (root / "modules").iterdir()
            if d.is_dir() and d.name.startswith("m")]
    mids = sorted(set(mids))
    modules_state = {m: "done" for m in mids} if modules is None else modules
    dep_map = deps or {m: [] for m in mids}
    snap = {
        "schema_version": 3, "run_id": run_id, "task": root.name,
        "updated_at": "2026-08-21T12:00:00+08:00", "status": "complete",
        "cause": "all_modules_done", "note": "测试快照",
        "modules": modules_state, "dependencies": dep_map,
        "failure_counts": {m: 0 for m in mids},
        "per_module": {m: {"executor_round": 1, "auditor_round": 1, "executor_id": "E1",
                           "executor_switches": 0, "block_count": 0, "block_total": 0,
                           "stall_count": 0, "root": "", "reason": "", "last_verdict": "pass"}
                       for m in mids},
        "needs_human": [], "completed_order": mids,
        "budget_used_tokens": 0, "last_seq": 0,
    }
    (root / "总日志").mkdir(parents=True, exist_ok=True)
    (root / "总日志" / "快照.json").write_text(
        json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")


def set_review_status_done(root: Path) -> None:
    from fw_runner.review import set_values
    for mid in [d.name.split("-", 1)[0] for d in (root / "modules").iterdir()
                if d.is_dir() and d.name.startswith("m")]:
        set_values(module_dir(root, mid) / "REVIEW.md", status="done", executor_round="1",
                   auditor_round="1")
