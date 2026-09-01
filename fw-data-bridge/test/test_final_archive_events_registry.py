"""m01 收官轮新增测试：runs 归档端点（POST/DELETE）、runs 级事件（run.start/run.archived）、
tree/timeline/usage/reply 按注册表解析 task_dir、fw-run.sh 注册表登记段。

验收对照（final_block acceptance，唯一权威）：
1. registry 读写契约（上一块）已覆盖；本块补 archive_run 幂等归档 + 列表不再显示
2. /api/runs/{id}/archive（POST/DELETE）幂等标记 archived，列表不再显示该 run
3. tree/timeline/usage/reply 端点按注册表解析 task_dir；注册表未命中确定性空降级；
   注册表缺失回落默认 task_dir（既有回落测试已覆盖）
4. events 端点在桥内补 runs 级事件（run.start / run.archived），新 run 出现、归档均产出；
   task.update 增量仍在（既有 test_usage_events_health 覆盖）
5. fw-run.sh 注册表登记段可独立运行（bash + python3）且落盘契约字段/ts 格式正确
"""
import json
import os
import shlex
import subprocess

from conftest import make_registry, make_snapshot  # noqa: F401


def _snapshot(run_id="run-a", status="active", needs_human=None):
    """构造真实快照（写 总日志/快照.json）。"""
    return {
        "schema_version": 4,
        "run_id": run_id,
        "task": "t",
        "status": status,
        "cause": "done",
        "updated_at": "2026-08-29T00:00:00+00:00",
        "modules": {"m01": "done"},
        "dependencies": {"m01": []},
        "per_module": {"m01": {"executor_round": 1}},
        "needs_human": needs_human if needs_human is not None else [],
    }


def _reg_env(monkeypatch, tmp_path, records):
    """写注册表并让环境变量指向它，返回注册表路径。"""
    path = make_registry(tmp_path, records)
    monkeypatch.setenv("AUTOKNIT_RUNS_REGISTRY", path)
    return path


# ============================================================ runs 归档端点 ----
def test_runs_archive_post_http(live_server, tmp_path, monkeypatch):
    """POST /api/runs/{id}/archive 幂等标记 archived，列表不再显示该 run。"""
    client, _ = live_server
    task_dir = make_snapshot(tmp_path, _snapshot("run-a", status="active"))
    _reg_env(monkeypatch, tmp_path, [
        {"run_id": "run-a", "task_dir": task_dir, "task": "t", "status": "active"},
    ])

    # 归档前：列表显示
    status, body = client.get("/api/runs")
    assert status == 200 and [it["run_id"] for it in body] == ["run-a"]

    # POST 归档
    status, body = client.post("/api/runs/run-a/archive")
    assert status == 200
    assert body == {"run_id": "run-a", "status": "archived", "ok": True}

    # 列表不再显示
    status, body = client.get("/api/runs")
    assert status == 200 and body == []

    # 幂等：重复 POST 仍成功
    status, body = client.post("/api/runs/run-a/archive")
    assert status == 200 and body == {"run_id": "run-a", "status": "archived", "ok": True}


def test_runs_archive_delete_http(live_server, tmp_path, monkeypatch):
    """DELETE /api/runs/{id}/archive 与 POST 等价：幂等标记 archived。"""
    client, _ = live_server
    task_dir = make_snapshot(tmp_path, _snapshot("run-a", status="active"))
    _reg_env(monkeypatch, tmp_path, [
        {"run_id": "run-a", "task_dir": task_dir, "task": "t", "status": "active"},
    ])
    status, body = client.delete("/api/runs/run-a/archive")
    assert status == 200
    assert body == {"run_id": "run-a", "status": "archived", "ok": True}
    status, body = client.delete("/api/runs/run-a/archive")
    assert status == 200 and body["ok"] is True
    status, body = client.get("/api/runs")
    assert body == []


def test_runs_archive_miss_and_empty(live_server, tmp_path, monkeypatch):
    """未命中/空 run_id → 400 确定性 ok=False。"""
    client, _ = live_server
    _reg_env(monkeypatch, tmp_path, [
        {"run_id": "run-a", "task_dir": "/x", "task": "t", "status": "active"},
    ])
    status, body = client.post("/api/runs/nope/archive")
    assert status == 400 and body["ok"] is False


def test_runs_archive_data_source(tmp_path, monkeypatch):
    """数据源层：registry.archive_run 幂等标记 archived。"""
    from fwapi import registry as reg

    task_dir = make_snapshot(tmp_path, _snapshot("run-a"))
    _reg_env(monkeypatch, tmp_path, [
        {"run_id": "run-a", "task_dir": task_dir, "task": "t", "status": "active"},
    ])
    res = reg.archive_run("run-a")
    assert res == {"run_id": "run-a", "status": "archived", "ok": True}
    assert reg.get_record("run-a")["status"] == "archived"
    # 幂等
    assert reg.archive_run("run-a")["ok"] is True
    # 未命中
    assert reg.archive_run("nope")["ok"] is False
    # 空
    assert reg.archive_run("")["ok"] is False


# ============================================================ events runs 级事件 ----
def test_events_run_start_new_run(live_server, tmp_path, monkeypatch):
    """新 run 出现 → run.start 事件（status 携带注册表状态）。"""
    from fwapi import registry as reg

    client, _ = live_server
    task_dir = make_snapshot(tmp_path, _snapshot("run-a"))
    _reg_env(monkeypatch, tmp_path, [
        {"run_id": "run-a", "task_dir": task_dir, "task": "t", "status": "active"},
    ])
    # 首次探测：run-a 出现 → run.start
    status, body = client.get("/api/events")
    assert status == 200
    assert any(e["type"] == "run.start" and e["run_id"] == "run-a"
               and e["status"] == "active" for e in body)

    # 新增 run-b → run.start（run-a 状态不变不重复事件）
    reg.upsert_record({"run_id": "run-b", "task_dir": task_dir, "task": "t", "status": "active"})
    status, body = client.get("/api/events")
    assert any(e["type"] == "run.start" and e["run_id"] == "run-b" for e in body)


def test_events_run_archived(live_server, tmp_path, monkeypatch):
    """归档 → run.archived 事件。"""
    client, _ = live_server
    task_dir = make_snapshot(tmp_path, _snapshot("run-a"))
    _reg_env(monkeypatch, tmp_path, [
        {"run_id": "run-a", "task_dir": task_dir, "task": "t", "status": "active"},
    ])
    _, body = client.get("/api/events")
    max_seq = max(e["seq"] for e in body)

    client.post("/api/runs/run-a/archive")
    status, body = client.get(f"/api/events?since={max_seq}")
    assert status == 200
    assert any(e["type"] == "run.archived" and e["run_id"] == "run-a"
               and e["status"] == "archived" for e in body)


def test_events_runs_and_task_update_coexist(live_server, tmp_path, monkeypatch):
    """task.update（既有）与 run.start（新增）可同桶并存、按 seq 升序。"""
    from conftest import make_task_dir

    client, _ = live_server
    task_dir = make_task_dir(tmp_path, [{
        "run_id": "run-mock", "stage": "executor", "task_name": "旧",
        "module_states": {}, "urgency": 1, "needs_human": False,
        "consumption": {},
    }])
    _reg_env(monkeypatch, tmp_path, [
        {"run_id": "run-mock", "task_dir": task_dir, "task": "旧", "status": "active"},
    ])
    status, body = client.get(f"/api/events?task_dir={task_dir}")
    assert status == 200
    types = {e["type"] for e in body}
    assert "task.update" in types and "run.start" in types
    seqs = [e["seq"] for e in body]
    assert seqs == sorted(seqs)


# ============================================================ 按注册表解析 task_dir ----
def test_tree_resolves_registry_task_dir(tmp_path, monkeypatch):
    """tree 按注册表定位 task_dir；请求级 task_dir 无关；注册表未命中 null。"""
    from fwapi.dsh import task as ts

    task_dir = make_snapshot(tmp_path, _snapshot("run-a"))
    _reg_env(monkeypatch, tmp_path, [
        {"run_id": "run-a", "task_dir": task_dir, "task": "t", "status": "active"},
    ])
    tree = ts.get_run_tree("/wrong/dir", "run-a")
    assert tree is not None and tree["run_id"] == "run-a"
    # 注册表存在但未命中 → null
    assert ts.get_run_tree("/wrong/dir", "run-nope") is None
    # 注册表含 run-a 但登记 task_dir 无效 → null
    reg_path = _reg_env(monkeypatch, tmp_path, [
        {"run_id": "run-a", "task_dir": "/no/such/dir", "task": "t", "status": "active"},
    ])
    assert ts.get_run_tree("", "run-a") is None


def test_timeline_resolves_registry_task_dir(tmp_path, monkeypatch):
    """timeline 按注册表定位 task_dir；未命中确定性 []。"""
    from fwapi.dsh import task as ts

    task_dir = make_snapshot(tmp_path, _snapshot("run-a"))
    log_dir = os.path.join(task_dir, "总日志")
    with open(os.path.join(log_dir, "dispatch.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"seq": 1, "ts": "T1", "run_id": "run-a",
                             "event": "run.start", "module": None, "detail": {}}) + "\n")
    _reg_env(monkeypatch, tmp_path, [
        {"run_id": "run-a", "task_dir": task_dir, "task": "t", "status": "active"},
    ])
    assert ts.get_run_timeline("/wrong/dir", "run-a")[0]["event"] == "run.start"
    assert ts.get_run_timeline("/wrong/dir", "run-nope") == []


def test_usage_resolves_registry_task_dir(tmp_path, monkeypatch):
    """usage 按注册表定位 task_dir；未命中确定性空降级。"""
    from fwapi.dsh import usage as us

    task_dir = make_snapshot(tmp_path, _snapshot("run-a"))
    _reg_env(monkeypatch, tmp_path, [
        {"run_id": "run-a", "task_dir": task_dir, "task": "t", "status": "active"},
    ])
    # 定位到注册表登记 task_dir（快照有 m01 可拆 → 非「无拆分数据」），请求级 task_dir 无关
    body = us.run_usage("/wrong/dir", "run-a")
    assert body["no_split"] == ""
    assert "m01" in body["per_module"]
    # 注册表存在但未命中 → 确定性空降级
    assert us.run_usage("/wrong/dir", "run-nope")["no_split"] == "无拆分数据"


def test_reply_resolves_registry_task_dir(tmp_path, monkeypatch):
    """reply 按注册表定位 task_dir（经 get_run_tree 解析）；写对快照所在目录。"""
    from fwapi.dsh import reply as rp

    task_dir = make_snapshot(tmp_path, _snapshot("run-a", needs_human=["m01"]))
    _reg_env(monkeypatch, tmp_path, [
        {"run_id": "run-a", "task_dir": task_dir, "task": "t", "status": "active"},
    ])
    res = rp.reply("/wrong/dir", "run-a", {"command": "continue"})
    assert res["success"] is True
    assert os.path.isfile(os.path.join(task_dir, "needs_human", "reply.md"))


def test_tree_http_resolves_registry(live_server, tmp_path, monkeypatch):
    """HTTP 层：tree 按注册表解析 task_dir，请求级 task_dir 无关。"""
    client, _ = live_server
    task_dir = make_snapshot(tmp_path, _snapshot("run-a"))
    _reg_env(monkeypatch, tmp_path, [
        {"run_id": "run-a", "task_dir": task_dir, "task": "t", "status": "active"},
    ])
    status, body = client.get("/api/runs/run-a/tree?task_dir=/wrong/dir")
    assert status == 200
    assert isinstance(body, dict) and body["run_id"] == "run-a"
    status, body = client.get("/api/runs/run-nope/tree?task_dir=/wrong/dir")
    assert status == 200 and body is None


# ============================================================ fw-run.sh 登记段 ----
def test_fw_run_registry_segment(tmp_path):
    """fw-run.sh 登记段可独立运行，落盘契约字段/ts 格式/幂等更新正确。"""
    segment = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "contrib", "fw-run.sh.registry-segment.sh",
    )
    assert os.path.isfile(segment), f"登记段缺失: {segment}"
    reg_path = tmp_path / "fw-run-reg" / "runs.json"
    reg = shlex.quote(str(reg_path))
    cmd = (
        f"source {shlex.quote(segment)} && "
        f"AUTOKNIT_RUNS_REGISTRY={reg} fw_run_registry_upsert 'run-z' '{tmp_path}/task-z' '样例' active && "
        f"AUTOKNIT_RUNS_REGISTRY={reg} fw_run_registry_upsert 'run-z' '{tmp_path}/task-z' '样例' complete"
    )
    proc = subprocess.run(["bash", "-c", cmd], capture_output=True, timeout=30)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")

    payload = json.loads(reg_path.read_text(encoding="utf-8"))
    assert set(payload.keys()) == {"runs"}
    assert len(payload["runs"]) == 1
    rec = payload["runs"][0]
    assert set(rec.keys()) == {
        "run_id", "task_dir", "task", "status", "started_at", "updated_at",
    }
    assert rec["run_id"] == "run-z"
    assert rec["status"] == "complete"  # 第二次更新生效（幂等覆盖）
    assert rec["task_dir"] == f"{tmp_path}/task-z"
    assert "T" in rec["started_at"] and "+00:00" in rec["started_at"]
    assert "T" in rec["updated_at"] and "+00:00" in rec["updated_at"]


def test_fw_run_registry_segment_rejects_bad_status(tmp_path):
    """登记段拒绝白名单外状态（不落盘），返回非 0。"""
    segment = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "contrib", "fw-run.sh.registry-segment.sh",
    )
    reg = shlex.quote(str(tmp_path / "runs.json"))
    cmd = (
        f"source {shlex.quote(segment)} && "
        f"AUTOKNIT_RUNS_REGISTRY={reg} fw_run_registry_upsert 'run-z' '{tmp_path}/task-z' 't' paused"
    )
    proc = subprocess.run(["bash", "-c", cmd], capture_output=True, timeout=30)
    assert proc.returncode != 0
    assert not os.path.exists(str(tmp_path / "runs.json"))
