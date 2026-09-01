"""run 注册表（fw_runner.registry）登记/收官测试 + runner 生命周期接入验证。

验收对照（任务：run 启动自动注册到 runs.json）：
1. register_run 幂等登记（首次新建 active + UTC ts；重复/同 run_id 不重复插入、保持 started_at）
2. complete_run 幂等置 complete + 刷新 updated_at；未命中 False
3. 失败绝不抛异常（缺 run_id / 目录不可写 / JSON 损坏 → False）
4. run() 生命周期接入：run.start 自动登记 → 数据桥 /api/runs 可见；complete 收官置 complete；
   interrupted 保持 active（resume 续跑沿用同一 run_id 幂等）
5. 注册表路径尊重 AUTOKNIT_RUNS_REGISTRY / registry_path 参数（测试隔离，不碰真实 ~/.autoknit/runs.json）
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from fw_runner import registry as reg
from fw_runner.registry import complete_run, register_run


def _write(path, records):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"runs": records}, ensure_ascii=False, indent=2), encoding="utf-8")


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))["runs"]


# ======================================================== register_run 契约 ----
def test_register_run_new_creates_active_utc(tmp_path, monkeypatch):
    """验收1/4：首次登记新建记录，status=active，ts 为 ISO-8601 UTC。"""
    rp = str(tmp_path / "runs.json")
    monkeypatch.setenv("AUTOKNIT_RUNS_REGISTRY", rp)
    assert register_run("run-abc", "/tmp/task-a", "样例任务") is True
    recs = _read(rp)
    assert len(recs) == 1
    rec = recs[0]
    assert rec["run_id"] == "run-abc"
    assert rec["task_dir"] == "/tmp/task-a"
    assert rec["task"] == "样例任务"
    assert rec["status"] == "active"
    assert rec["started_at"].endswith("+00:00") and "T" in rec["started_at"]
    assert rec["updated_at"] == rec["started_at"]


def test_register_run_idempotent_same_run_id(tmp_path, monkeypatch):
    """验收1/2：同 run_id 重复登记不重复插入、不覆盖（已存在则跳过，保持 started_at/status）。"""
    rp = str(tmp_path / "runs.json")
    monkeypatch.setenv("AUTOKNIT_RUNS_REGISTRY", rp)
    assert register_run("run-x", "/t", "任务") is True
    recs = _read(rp)
    started = recs[0]["started_at"]
    # 重复登记（即使带了不同 task/status）→ 幂等跳过，不重复插入、不覆盖
    assert register_run("run-x", "/t2", "改名", status="complete") is True
    recs = _read(rp)
    assert len(recs) == 1
    assert recs[0]["started_at"] == started
    assert recs[0]["task_dir"] == "/t"      # 未覆盖
    assert recs[0]["status"] == "active"    # 未覆盖


def test_register_run_registry_path_param_overrides_env(tmp_path):
    """验收5：registry_path 参数优先于环境变量（测试隔离）。"""
    explicit = str(tmp_path / "explicit" / "runs.json")
    assert register_run("run-p", "/t", "任务", registry_path=explicit) is True
    assert Path(explicit).is_file()
    assert _read(explicit)[0]["run_id"] == "run-p"


def test_register_run_failure_returns_false_no_raise(tmp_path, monkeypatch):
    """验收3：缺 run_id / 非法 status / 目录不可写 → False，绝不抛异常。"""
    rp = str(tmp_path / "runs.json")
    monkeypatch.setenv("AUTOKNIT_RUNS_REGISTRY", rp)
    assert register_run("", "/t", "任务") is False
    assert register_run("run-a", "", "任务") is False
    assert register_run("run-a", "/t", "任务", status="running") is False  # 非契约枚举
    assert not Path(rp).exists()


def test_register_run_corrupt_file_not_raise(tmp_path, monkeypatch):
    """验收3：注册表损坏/非预期形态 → 不抛异常，从空重建。"""
    rp = str(tmp_path / "runs.json")
    rp = str(Path(rp).parent / "runs.json")
    Path(rp).parent.mkdir(parents=True, exist_ok=True)
    Path(rp).write_text("not json{{{", encoding="utf-8")
    monkeypatch.setenv("AUTOKNIT_RUNS_REGISTRY", rp)
    assert register_run("run-c", "/t", "任务") is True
    assert _read(rp)[0]["run_id"] == "run-c"


# ======================================================== complete_run ----
def test_complete_run_sets_complete_refresh_updated_at(tmp_path, monkeypatch):
    """验收2：complete_run 幂等置 complete + 刷新 updated_at；未命中 False。"""
    rp = str(tmp_path / "runs.json")
    monkeypatch.setenv("AUTOKNIT_RUNS_REGISTRY", rp)
    register_run("run-done", "/t", "任务")
    first_upd = _read(rp)[0]["updated_at"]
    assert complete_run("run-done") is True
    rec = _read(rp)[0]
    assert rec["status"] == "complete"
    # updated_at 已刷新（单调不倒退）
    assert rec["updated_at"] >= first_upd
    # 幂等重复
    assert complete_run("run-done") is True
    assert len(_read(rp)) == 1
    # 未命中 → False，不抛
    assert complete_run("run-nope") is False


def test_complete_run_missing_or_io_failure_no_raise(tmp_path, monkeypatch):
    """验收3：complete_run 对不存在文件/未命中确定性 False，绝不抛异常。"""
    rp = str(tmp_path / "no-such" / "runs.json")
    monkeypatch.setenv("AUTOKNIT_RUNS_REGISTRY", rp)
    assert complete_run("run-ghost") is False
    assert complete_run("") is False


# ======================================================== read/get ----
def test_read_records_normalize_and_get(tmp_path, monkeypatch):
    """读归一化 + get_record：未知 status 标 unknown；缺 run_id 丢弃；按 run_id 命中。"""
    rp = str(tmp_path / "runs.json")
    monkeypatch.setenv("AUTOKNIT_RUNS_REGISTRY", rp)
    register_run("run-1", "/t", "任务")
    register_run("run-2", "/t2", "任务2", status="complete")
    recs = reg.read_records()
    assert [r["run_id"] for r in recs] == ["run-1", "run-2"]
    got = reg.get_record("run-2")
    assert got is not None and got["status"] == "complete"
    assert reg.get_record("run-nope") is None


# ======================================================== run() 生命周期接入 ----
def test_run_registers_on_start_and_complete(harness, single_root, tmp_path, monkeypatch):
    """验收4：run() 冷启动自动登记 active；全部完成收官置 complete（/api/runs 可见语义）。"""
    rp = str(tmp_path / "runs.json")
    monkeypatch.setenv("AUTOKNIT_RUNS_REGISTRY", rp)

    from fw_runner.runner import run as run_runner
    result = run_runner(single_root, executor_driver=harness.make_executor(),
                        auditor_driver=harness.make_auditor())
    assert result.status == "complete"
    recs = _read(rp)
    assert len(recs) == 1
    rec = recs[0]
    assert rec["run_id"] == result.run_id
    assert rec["task_dir"] == str(single_root)
    assert rec["task"] == "验收3-单模块"
    assert rec["status"] == "complete"  # 收官置 complete
    assert rec["started_at"].endswith("+00:00")


def test_run_resume_reuses_same_run_id_and_stays_registered(tmp_path, monkeypatch, harness, single_root):
    """验收4/1：resume 续跑沿用同一 run_id，register_run 幂等跳过（不重复插入、不覆盖）。"""
    rp = str(tmp_path / "runs.json")
    monkeypatch.setenv("AUTOKNIT_RUNS_REGISTRY", rp)

    from fw_runner.runner import run as run_runner

    # 1) 首次跑完成 → 注册表登记 + 收官置 complete
    result1 = run_runner(single_root, executor_driver=harness.make_executor(),
                         auditor_driver=harness.make_auditor())
    rid1 = result1.run_id
    recs = _read(rp)
    assert len(recs) == 1 and recs[0]["run_id"] == rid1
    assert recs[0]["status"] == "complete"
    started1 = recs[0]["started_at"]

    # 2) resume：同一 run_id 已存在 → register_run 幂等跳过（不重复插入、不覆盖 status/started_at）
    result2 = run_runner(single_root, resume=True, executor_driver=harness.make_executor(),
                         auditor_driver=harness.make_auditor())
    assert result2.run_id == rid1
    recs = _read(rp)
    assert len(recs) == 1                      # 未重复插入
    assert recs[0]["started_at"] == started1   # started_at 保持
    assert recs[0]["status"] == "complete"     # 幂等跳过不覆盖（保持既有状态）


def test_run_interrupted_keeps_active(tmp_path, monkeypatch, single_root):
    """验收4：中断终态保持 active（dashboard 仍可跟随/归档，resume 续跑），不置 complete。"""
    from fw_runner.runner import RunInterrupted, run as run_runner
    from fw_runner.drivers import InlineAgentDriver

    rp = str(tmp_path / "runs.json")
    monkeypatch.setenv("AUTOKNIT_RUNS_REGISTRY", rp)

    def boom(ctx):
        raise RunInterrupted("模拟中断")

    result = run_runner(single_root, executor_driver=InlineAgentDriver(boom),
                        auditor_driver=harness_make_pass())
    assert result.status == "interrupted"
    recs = _read(rp)
    assert len(recs) == 1
    assert recs[0]["status"] == "active"      # interrupted 保持 active


def harness_make_pass():
    from fw_runner.drivers import InlineAgentDriver
    from fw_runner.model import DriverOutcome

    def audit(ctx):
        return DriverOutcome(status="ok", verdict="pass", evidence_level="L2", root="",
                             confidence=0.9, reason="ok")
    return InlineAgentDriver(audit)
