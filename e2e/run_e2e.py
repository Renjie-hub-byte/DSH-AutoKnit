#!/usr/bin/env python3.11
"""framework-v1 需求7 端到端示例编排器（可重复运行、默认零冲突）。

全流程（严格按已审计六模块的 CLI/钩子消费，不改任何已审计模块源码）：
  1. fw-protocol  校验 e2e/task.yaml（CLI，exit 0 / effective）
  2. fw-scaffold  生成任务目录树（CLI，--output 独立运行目录）
  3. fw-runner    Python API run() 组合：真实 BudgetGate（预算闸门 warn 路径）
                  + FwIntegrateHook（集成验收钩子）+ e2e 脚本化 executor/auditor 驱动
                  （m02 升级链：E1 block×2 → 换 E2 → 修复通过；≤ max_parallel 并行）
  4. 证据自检：并行度 / 依赖等待 / 升级链留痕 / 预算 warn+排行 / 集成基线 / 事件 seq / 快照 v3
  5. fw-budget    status（预算报告：warn 相位 + 模块消耗排行）
  6. fw-integrate complete（完成报告 完成报告.md + 归档 archived/）

用法：
    python3.11 e2e/run_e2e.py [--output DIR] [--json]
    # --output 缺省 = e2e/runs/run-<时间戳>/（每次运行独立目录，可重复执行）
退出码：0=全部通过；1=任一环节失败（证据自检 FAIL 明细见输出与 e2e-evidence.md）。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

FW1 = Path(__file__).resolve().parent.parent          # framework-v1/
E2E = FW1 / "e2e"
TASK_YAML = E2E / "task.yaml"
DRIVERS = E2E / "drivers"
PY = sys.executable

# 运行目录里的关键相对路径（与 fw-scaffold 目录规范 v2 对齐）
SNAPSHOT_REL = "总日志/快照.json"
DISPATCH_REL = "总日志/dispatch.jsonl"
INTEGRATION_REL = "总日志/integration.jsonl"


def _stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def _run_cli(argv: List[str], timeout: int = 180) -> subprocess.CompletedProcess:
    """跑已审计模块的可执行入口（shebang python3.11），返回 CompletedProcess。"""
    return subprocess.run([PY] + argv, capture_output=True, text=True, timeout=timeout)


def _load_jsonl(path: Path) -> List[Dict]:
    out: List[Dict] = []
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _iso_to_epoch(ts: str) -> float:
    return _dt.datetime.fromisoformat(ts).timestamp()


class Evidence:
    """证据自检表：逐项 (名称, 期望, 实际, 通过) 落盘为 e2e-evidence.md。"""

    def __init__(self) -> None:
        self.items: List[Tuple[str, str, str, bool]] = []

    def add(self, name: str, expected: str, actual: str, ok: bool) -> None:
        self.items.append((name, expected, actual, ok))
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {name}: 期望={expected} 实际={actual}")

    @property
    def all_ok(self) -> bool:
        return all(ok for _, _, _, ok in self.items)

    def render(self) -> str:
        lines = ["# framework-v1 端到端示例 —— 证据自检（e2e-evidence.md）", ""]
        lines.append(f"生成时间: {_dt.datetime.now().astimezone().isoformat(timespec='seconds')}")
        lines.append("")
        for name, expected, actual, ok in self.items:
            lines.append(f"- [{'x' if ok else ' '}] {name}（期望 {expected}；实际 {actual}）")
        lines.append("")
        lines.append(f"总体: {'全部 PASS' if self.all_ok else '存在 FAIL'}")
        return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="run_e2e.py", description="framework-v1 端到端示例编排器")
    ap.add_argument("--output", default=None, help="运行父目录（缺省=e2e/runs/run-<时间戳>/）")
    ap.add_argument("--json", action="store_true", help="最终输出机器可解析 JSON 摘要")
    args = ap.parse_args(argv)

    output = Path(args.output).expanduser() if args.output else (E2E / "runs" / f"run-{_stamp()}")
    output.mkdir(parents=True, exist_ok=True)
    print(f"运行目录: {output}")

    # ---- 包路径（runner/integrate Python API 用；与已审计模块 bin 的 sys.path 引导同构）----
    for sub in ("fw-runner", "fw-protocol", "fw-scaffold", "fw-budget", "fw-integrate"):
        p = str((FW1 / sub).resolve())
        if p not in sys.path:
            sys.path.insert(0, p)

    ev = Evidence()
    summary: Dict = {}

    # ================= STEP 1: fw-protocol 校验 =================
    print("\n[STEP 1] fw-protocol 校验任务书")
    p = _run_cli([str(FW1 / "fw-protocol" / "bin" / "fw-protocol"), str(TASK_YAML), "--json"])
    try:
        proto = json.loads(p.stdout)
    except json.JSONDecodeError:
        proto = {}
    proto_ok = p.returncode == 0 and proto.get("ok") is True and proto.get("status") == "pass"
    ev.add("fw-protocol 校验通过", "exit 0 / ok=true / status=pass",
           f"exit={p.returncode} ok={proto.get('ok')} status={proto.get('status')}", proto_ok)
    if not proto_ok:
        print(proto.get("errors"), file=sys.stderr)

    # ================= STEP 2: fw-scaffold 生成 =================
    print("\n[STEP 2] fw-scaffold 生成任务目录树")
    p = _run_cli([str(FW1 / "fw-scaffold" / "bin" / "fw-scaffold"),
                  str(TASK_YAML), "-o", str(output), "--json"])
    try:
        scaf = json.loads(p.stdout)
    except json.JSONDecodeError:
        scaf = {}
    root = Path(scaf.get("root") or "") if scaf.get("root") else None
    scaffold_ok = p.returncode == 0 and root is not None and root.is_dir()
    ev.add("fw-scaffold 生成目录树", "exit 0 / root 存在",
           f"exit={p.returncode} root={root}", scaffold_ok)
    if not scaffold_ok:
        print(p.stdout, p.stderr, file=sys.stderr)

    required_dirs = ["总日志", "modules", "shared", "认知", "contracts"]
    missing_dirs = [d for d in required_dirs if not (root / d).is_dir()]
    ev.add("目录树完整（总日志/modules/shared/认知/contracts）", "全部存在",
           f"缺失={missing_dirs or '无'}", not missing_dirs)
    for three in ("dispatch.jsonl", "integration.jsonl", "快照.json"):
        ok = (root / "总日志" / three).is_file()
        ev.add(f"总日志/{three} 就绪", "存在", "存在" if ok else "缺失", ok)

    # ================= STEP 3: fw-runner（BudgetGate + FwIntegrateHook）=================
    print("\n[STEP 3] fw-runner 编排（并行调度 + 升级链 + 预算闸门 + 集成钩子）")
    import yaml as _yaml
    from fw_runner.budget_hook import BudgetGate
    from fw_runner.drivers import ScriptedAgentDriver
    from fw_runner.runner import run as runner_run
    from fw_integrate.hook import FwIntegrateHook

    eff = _yaml.safe_load((root / "task.yaml").read_text(encoding="utf-8"))
    b = eff["budget"]
    gate = BudgetGate(
        max_tokens=int(b["max_tokens"]),
        warn_at=float(b["warn_at"]),
        stop_at=float(b["stop_at"]),
        per_module_max_tokens=int(b.get("per_module_max_tokens") or b["max_tokens"]),
    )
    executor_driver = ScriptedAgentDriver(f"{PY} {DRIVERS / 'e2e-executor.py'}", role="executor")
    auditor_driver = ScriptedAgentDriver(f"{PY} {DRIVERS / 'e2e-auditor.py'}", role="auditor")

    t0 = time.monotonic()
    result = runner_run(
        root,
        executor_driver=executor_driver,
        auditor_driver=auditor_driver,
        budget_gate=gate,
        integration_hook=FwIntegrateHook(),
        mode="speed_first",
    )
    dur = time.monotonic() - t0
    (output / "runner-result.json").write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  run_id={result.run_id} status={result.status} 耗时={dur:.2f}s "
          f"tokens_used={result.tokens_used} seq_events={result.seq_events}")

    run_ok = result.status == "complete"
    ev.add("runner 全部完成（status=complete）", "complete",
           f"{result.status}(exit_reason={result.exit_reason})", run_ok)
    ev.add("已完成模块集", "[m01, m02, m03]",
           str(sorted(result.completed)), sorted(result.completed) == ["m01", "m02", "m03"])

    # ---- 事件流 / 快照 ----
    snapshot = {}
    snap_path = root / SNAPSHOT_REL
    if snap_path.is_file():
        snapshot = json.loads(snap_path.read_text(encoding="utf-8"))
    events = _load_jsonl(root / DISPATCH_REL)

    seqs = [int(e["seq"]) for e in events if "seq" in e]
    seq_ok = (seqs == sorted(seqs) and len(seqs) == len(set(seqs)) and len(seqs) >= 10)
    ev.add("事件流 seq 严格单调且 ≥10 条", "seq 单调 + 无重复 + 条数≥10",
           f"seqs={len(seqs)} 单调={seqs == sorted(seqs) and len(seqs) == len(set(seqs))}", seq_ok)

    snap_ok = (snapshot.get("status") == "complete"
               and snapshot.get("cause") == "all_modules_done"
               and str(snapshot.get("schema_version")) == "3")
    ev.add("快照 schema v3 / status=complete / cause=all_modules_done",
           "schema_version=3, complete, all_modules_done",
           f"v={snapshot.get('schema_version')} status={snapshot.get('status')} cause={snapshot.get('cause')}",
           snap_ok)

    # ---- 并行 ≤ max_parallel + 依赖等待（用 ts 计算活动模块数）----
    max_parallel = int((eff.get("runtime") or {}).get("max_parallel") or 3)
    starts: Dict[str, float] = {}
    ends: Dict[str, float] = {}
    for e in events:
        mid = e.get("module")
        if not mid:
            continue
        ts = _iso_to_epoch(e["ts"])
        if e["event"] == "module.dispatch":
            starts.setdefault(mid, ts)
        elif e["event"] == "module.done":
            ends.setdefault(mid, ts)
    active_max = 0
    for e in events:
        ts = _iso_to_epoch(e["ts"])
        active = sum(1 for mid in starts
                     if starts[mid] <= ts and (mid not in ends or ends[mid] >= ts))
        active_max = max(active_max, active)
    ev.add(f"并行 ≤ max_parallel={max_parallel}", f"active_max ≤ {max_parallel}",
           f"active_max={active_max}", active_max <= max_parallel)

    dep_ok = True
    dep_lines = []
    for e in events:
        if e["event"] == "module.dispatch" and e["module"] in ("m02", "m03"):
            d_start = _iso_to_epoch(e["ts"])
            dep_lines.append(e["module"])
            if "m01" not in ends or ends["m01"] > d_start:
                dep_ok = False
    ev.add("依赖等待 m02/m03 在 m01 完成前不启动",
           "m02/m03 dispatch 于 m01 done 之后",
           f"m01_end={ends.get('m01') and _dt.datetime.fromtimestamp(ends['m01']).strftime('%H:%M:%S') or '?'} "
           f"m02/m03 已 wait, 顺序={dep_lines}, 满足={dep_ok}", dep_ok)

    # ---- 升级链：m02 block×2 → 换 E2 → 修复通过 ----
    m02_state = (snapshot.get("per_module") or {}).get("m02") or {}
    m02_status = (snapshot.get("modules") or {}).get("m02")
    up_ok = (m02_status == "done"
             and m02_state.get("block_total") == 2
             and m02_state.get("executor_switches") == 1
             and m02_state.get("executor_id") == "E2"
             and m02_state.get("executor_round") == 3
             and m02_state.get("auditor_round") == 3)
    ev.add("m02 升级链（block×2 → 换 E2 → 第3轮通过）",
           "modules.m02=done, block_total=2, switches=1, E2, rounds=3/3",
           f"status={m02_status} block_total={m02_state.get('block_total')} "
           f"switches={m02_state.get('executor_switches')} exec={m02_state.get('executor_id')} "
           f"r={m02_state.get('executor_round')}/{m02_state.get('auditor_round')}", up_ok)

    switch_ev = [e for e in events if e["event"] == "executor.switch" and e.get("module") == "m02"]
    handover_path = Path(str(switch_ev[0]["detail"].get("handover_bundle") or "")) if switch_ev else None
    handover_ok = bool(switch_ev) and handover_path is not None and handover_path.is_file()
    ev.add("m02 交接三件套（executor.switch 事件 + handover bundle 落盘）",
           "switch 事件存在 + handover-*.md 存在",
           f"switch_events={len(switch_ev)} bundle={handover_path and handover_path.name or '无'}",
           handover_ok)

    blocked_m02 = [e for e in events if e["event"] == "module.blocked" and e.get("module") == "m02"]
    ev.add("m02 打回事件 ×2（升级链留痕）", "module.blocked 2 条",
           f"module.blocked={len(blocked_m02)}（action={[e['detail'].get('action') for e in blocked_m02]}）",
           len(blocked_m02) == 2)

    # REVIEW.md 机器键（m02 终态）
    review_m02 = (root / "modules" / "m02-数据清洗" / "REVIEW.md")
    review_text = review_m02.read_text(encoding="utf-8") if review_m02.is_file() else ""
    kv = {}
    for line in review_text.splitlines():
        if ":" in line and not line.strip().startswith(("-", "#", ">", "|")):
            k, _, v = line.partition(":")
            k = k.strip()
            if k in ("status", "executor_round", "auditor_round", "block_total",
                     "executor_switches", "executor_id", "root", "confidence"):
                kv[k] = v.strip()
    review_ok = (kv.get("status") == "done" and kv.get("block_total") == "2"
                 and kv.get("executor_switches") == "1" and kv.get("executor_id") == "E2")
    ev.add("m02 REVIEW.md 机器键终态（status/block_total/executor_switches/executor_id）",
           "done / 2 / 1 / E2",
           f"status={kv.get('status')} block_total={kv.get('block_total')} "
           f"switches={kv.get('executor_switches')} exec={kv.get('executor_id')}", review_ok)

    # ---- 预算闸门：warn 路径 ----
    warn_evs = [e for e in events if e["event"] == "budget.warn"]
    ranking = warn_evs[-1]["detail"].get("ranking") if warn_evs else []
    warn_ok = bool(warn_evs) and [r["module"] for r in ranking][:3] == ["m02", "m01", "m03"]
    ev.add("预算闸门 warn 事件（70% 预警，含模块消耗排行）",
           "budget.warn ≥1 + ranking=[m02,m01,m03]",
           f"warn={len(warn_evs)} ranking={[r['module'] for r in ranking]} used="
           f"{warn_evs[-1]['detail']['budget']['used'] if warn_evs else '-'}",
           warn_ok)

    # ---- 集成钩子（runner 内）----
    integ = result.integration or {}
    integ_ok = integ.get("status") == "passed"
    bl = (integ.get("summary") or {}).get("baseline") or {}
    blcounts = bl.get("counts") or {}
    bl_ok = (blcounts.get("will_have_matched") == 3
             and blcounts.get("will_have_missing") == 0
             and blcounts.get("will_not_have_violation") == 0
             and blcounts.get("will_not_have_clean") == 2)
    ev.add("集成钩子 passed + 预测基线对照（matched=3, missing=0, clean=2, violation=0）",
           "passed + 全匹配",
           f"status={integ.get('status')} counts={blcounts}", integ_ok and bl_ok)

    integration_events = _load_jsonl(root / INTEGRATION_REL)
    int_ok = any(e.get("event") == "integration.check"
                 and (e.get("detail") or {}).get("status") == "passed"
                 for e in integration_events)
    ev.add("integration.jsonl 落 integration.check 事件（status=passed）",
           "≥1 条 passed", f"integration.check={sum(1 for e in integration_events if e.get('event')=='integration.check')}",
           int_ok)

    # ================= STEP 4: fw-budget status（预算报告证据） =================
    print("\n[STEP 4] fw-budget status（warn 相位 + 模块消耗排行）")
    p = _run_cli([str(FW1 / "fw-budget" / "bin" / "fw-budget"), "status", str(root), "--json"])
    try:
        brep = json.loads(p.stdout)
    except json.JSONDecodeError:
        brep = {}
    (output / "budget-report.json").write_text(
        json.dumps(brep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    budget_ok = (p.returncode == 0 and brep.get("phase") == "warned"
                 and brep.get("gate", {}).get("stop") is False
                 and brep.get("meter", {}).get("total") == 1300)
    meter_ranking = brep.get("meter", {}).get("ranking") or []
    ev.add("fw-budget status：phase=warned / stop=false / total=1300 / 排行降序",
           "warned, stop=false, total=1300",
           f"phase={brep.get('phase')} stop={brep.get('gate', {}).get('stop')} "
           f"total={brep.get('meter', {}).get('total')} source={brep.get('meter', {}).get('source')} "
           f"ranking={[r['module'] for r in meter_ranking]}",
           budget_ok)
    summary["budget"] = {"phase": brep.get("phase"), "total": brep.get("meter", {}).get("total"),
                         "ranking": [r["module"] for r in meter_ranking]}

    # ================= STEP 5: fw-integrate complete（完成报告 + 归档） =================
    print("\n[STEP 5] fw-integrate complete（完成报告 + 归档）")
    p = _run_cli([str(FW1 / "fw-integrate" / "bin" / "fw-integrate"), "complete",
                  str(root), "--reason", "需求7 端到端示例：3 模块含 1 次失败升级，全部通过",
                  "--json"])
    try:
        cres = json.loads(p.stdout)
    except json.JSONDecodeError:
        cres = {}
    complete_ok = p.returncode == 0 and cres.get("status") == "completed"
    ev.add("fw-integrate complete：完成报告 + 归档",
           "exit 0 / status=completed / archived_path 存在",
           f"exit={p.returncode} status={cres.get('status')} archive={cres.get('archived_path')}",
           complete_ok)

    archived = Path(str(cres.get("archived_path") or "")) if cres.get("archived_path") else None
    ark_ok = False
    if archived and archived.is_dir():
        comp_ok = (archived / "完成报告.md").is_file()
        arch_mark = (archived / "ARCHIVE.md").is_file()
        snap_arch = json.loads((archived / SNAPSHOT_REL).read_text(encoding="utf-8")) \
            if (archived / SNAPSHOT_REL).is_file() else {}
        ark_ok = comp_ok and arch_mark and snap_arch.get("status") == "archived" \
            and snap_arch.get("cause") == "completed"
        ev.add("归档树完整性（完成报告.md + ARCHIVE.md + 快照 archived/completed）",
               "三件齐",
               f"完成报告={comp_ok} ARCHIVE={arch_mark} snapshot={snap_arch.get('status')}/{snap_arch.get('cause')}",
               ark_ok)
    summary["archive"] = {"path": str(archived) if archived else None}

    # ================= 汇总 =================
    evidence_path = output / "e2e-evidence.md"
    evidence_path.write_text(ev.render(), encoding="utf-8")
    print(f"\n证据报告: {evidence_path}")
    print(f"任务根(归档前): {root}")
    print(f"档案目录: {archived}")
    print(f"总体: {'全部 PASS' if ev.all_ok else '存在 FAIL'}")

    summary.update({
        "ok": ev.all_ok and proto_ok and scaffold_ok,
        "run_id": result.run_id,
        "runner_status": result.status,
        "run_dir": str(output),
        "task_root": str(root),
        "archived": str(archived) if archived else None,
        "evidence": str(evidence_path),
        "checks": [{"name": n, "ok": o, "expected": e, "actual": a} for n, e, a, o in ev.items],
    })
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if (ev.all_ok and proto_ok and scaffold_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
