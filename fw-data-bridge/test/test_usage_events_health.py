"""fwapi.serve 收官轮新增端点测试：usage / events / health / 路径式归档 / 错误码。

对照任务书 remaining scope：
- 消耗汇总接口（dsh.usage.summary 透出到 /api/usage）
- 更完善的错误码与健康检查端点
- 可选事件推送（dsh.task.update 桥接，/api/events）
- POST /api/tasks/{run_id}/archive 路径式归档
"""
from urllib.parse import urlencode

from conftest import make_task_dir  # noqa: F401


def _sample_runs():
    return [
        {
            "run_id": "run-a",
            "stage": "executor",
            "task_name": "A",
            "module_states": {"m01": {"status": "running"}},
            "urgency": 8,
            "needs_human": True,
            "consumption": {"token_input": 100, "token_output": 50, "cache_hit": "hit", "duration_sec": 12},
        },
        {
            "run_id": "run-b",
            "stage": "planning",
            "task_name": "B",
            "module_states": {},
            "urgency": 2,
            "needs_human": False,
            "consumption": {"token_input": 10, "token_output": 5, "cache_hit": "no", "duration_sec": 2},
        },
    ]


def _url(task_dir, endpoint="/api/tasks", **query):
    """构造带 task_dir 的 URL；可额外附加 query 键（不会重复 `?`）。"""
    q = {"task_dir": task_dir}
    q.update(query)
    return endpoint + "?" + urlencode(q)


# ---------------------------------------------------------------- usage ----
def test_usage_http(live_server):
    """GET /api/usage 返回消耗汇总。"""
    client, make = live_server
    task_dir = make(_sample_runs())
    status, body = client.get(_url(task_dir, "/api/usage"))
    assert status == 200
    assert body["total_runs"] == 2
    assert body["total_token_input"] == 110
    assert body["total_token_output"] == 55
    assert body["total_duration_sec"] == 14
    assert body["cache_hit_runs"] == 1
    assert body["by_stage"]["executor"]["token_input"] == 100
    assert body["by_stage"]["planning"]["runs"] == 1


def test_usage_empty_when_dir_missing(live_server):
    """目录缺失 → usage 全 0，HTTP 200 不抛异常。"""
    client, _ = live_server
    status, body = client.get(_url("/no/such/dir", "/api/usage"))
    assert status == 200
    assert body["total_runs"] == 0
    assert body["total_token_input"] == 0
    assert body["by_stage"] == {}


def test_usage_data_source_aggregates(live_server, tmp_path):
    """数据源层 summary 直接聚合（不依赖 HTTP）。"""
    from fwapi.dsh import usage

    task_dir = make_task_dir(tmp_path, _sample_runs())
    body = usage.summary(task_dir)
    assert body["total_token_output"] == 55


# ---------------------------------------------------------------- health ----
def test_health_ok(live_server):
    """GET /api/health 恒 200，返回 status=ok 与版本。"""
    client, _ = live_server
    status, body = client.get("/api/health")
    assert status == 200
    assert body["status"] == "ok"
    assert body["service"] == "fwapi"
    assert isinstance(body["version"], str)


# ---------------------------------------------------------------- events ----
def test_events_incremental(live_server):
    """事件桥：list 拉取后 diff 产生 task.update，事件带递增 seq。"""
    client, make = live_server
    task_dir = make(_sample_runs())

    status, body = client.get(_url(task_dir, "/api/events"))
    assert status == 200
    # 首次探测：2 个 run 全部为新增事件
    assert len(body) == 2
    run_ids = {e["run_id"] for e in body}
    assert run_ids == {"run-a", "run-b"}
    seqs = [e["seq"] for e in body]
    assert seqs == sorted(seqs)
    assert all(e["type"] == "task.update" for e in body)
    max_seq = max(seqs)

    # 再查同状态 + since 游标越过已有事件：无新增事件
    status, body = client.get(_url(task_dir, "/api/events", since=str(max_seq)))
    assert body == []


def test_events_since_cursor(live_server):
    """since 游标：只返回 seq 更大的事件。"""
    client, make = live_server
    task_dir = make(_sample_runs())
    _, first = client.get(_url(task_dir, "/api/events"))
    max_seq = max(e["seq"] for e in first)

    # 修改 runs.json 让 stage 变化，再探测
    import json
    import os

    runs_path = os.path.join(task_dir, "总日志", "runs.json")
    with open(runs_path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    payload["runs"][0]["stage"] = "auditor"
    with open(runs_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)

    _, since = client.get(_url(task_dir, "/api/events", since=str(max_seq)))
    assert len(since) == 1
    assert since[0]["run_id"] == "run-a"
    assert since[0]["stage"] == "auditor"
    assert since[0]["seq"] > max_seq


# ------------------------------------------------------------- 路径式归档 ----
def test_archive_path_based(live_server):
    """POST /api/tasks/{run_id}/archive 写归档，archived 可读到。"""
    client, make = live_server
    task_dir = make(_sample_runs())

    status, body = client.post(f"/api/tasks/run-a/archive", {"task_dir": task_dir})
    assert status == 200
    assert body == {"ok": True, "run_id": "run-a", "archived": True}

    status, body = client.get(_url(task_dir, "/api/tasks/archived"))
    assert status == 200 and body == ["run-a"]

    # 幂等
    status, body = client.post(f"/api/tasks/run-a/archive", {"task_dir": task_dir})
    assert body == {"ok": True, "run_id": "run-a", "archived": True}


def test_archive_path_empty_id_returns_400(live_server):
    """路径式空 run_id（/api/tasks//archive）→ 400 bad_request。"""
    client, make = live_server
    task_dir = make(_sample_runs())
    status, body = client.post(f"/api/tasks//archive", {"task_dir": task_dir})
    assert status == 400
    assert body["ok"] is False


# ---------------------------------------------------------------- 错误码 ----
def test_unknown_endpoint_404(live_server):
    """未知端点 → 404 统一错误信封。"""
    client, _ = live_server
    status, body = client.get("/api/nope")
    assert status == 404
    assert body["error"] == "not_found"
    assert isinstance(body["message"], str)


def test_usage_post_not_allowed(live_server):
    """对 GET-only 端点发 POST → 404（路由不匹配）。"""
    client, _ = live_server
    status, body = client.post("/api/usage", {})
    assert status == 404
    assert body["error"] == "not_found"


# ============================================== dispatch.jsonl 真事件桥 + 长轮询 ----
def test_dispatch_events_bridge(tmp_path, monkeypatch):
    """dispatch.jsonl 增量桥：新行 → task.update；批内去抖；首见跳历史。"""
    import json as _json
    import os as _os
    import time as _time

    from fwapi.dsh import events as ev

    task_dir = str(tmp_path / "task-dispatch")
    log_dir = _os.path.join(task_dir, "总日志")
    _os.makedirs(log_dir, exist_ok=True)
    path = _os.path.join(log_dir, "dispatch.jsonl")

    def _append(rows):
        with open(path, "a", encoding="utf-8") as fh:
            for r in rows:
                fh.write(_json.dumps(r, ensure_ascii=False) + "\n")

    # 首见桶：历史行跳过（不重放），offset 置为当前文件尾。
    _append([
        {"seq": 1, "ts": "t", "run_id": "run-x", "event": "run.start", "module": None},
        {"seq": 2, "ts": "t", "run_id": "run-x", "event": "module.dispatch", "module": "m01"},
    ])
    ev.reset_buckets()
    assert ev.check_dispatch_events(task_dir) == []

    # 追加新行 → 契约内事件映射为 task.update；批内同 run 去抖。
    _append([
        {"seq": 3, "ts": "t", "run_id": "run-x", "event": "executor.round.end", "module": "m01"},
        {"seq": 4, "ts": "t", "run_id": "run-x", "event": "auditor.round.end", "module": "m01"},
        {"seq": 5, "ts": "t", "run_id": "run-y", "event": "module.done", "module": "m02"},
        {"seq": 6, "ts": "t", "run_id": "run-y", "event": "not.in.contract", "module": "m02"},
    ])
    emitted = ev.check_dispatch_events(task_dir)
    assert [(e["run_id"], e["type"]) for e in emitted] == [
        ("run-x", "task.update"), ("run-y", "task.update")]

    # 无新行 → 无事件（幂等）。
    assert ev.check_dispatch_events(task_dir) == []

    # 事件进入桶缓冲，events_since 可取。
    got = ev.events_since(task_dir, 0)
    assert any(e["run_id"] == "run-x" and e["type"] == "task.update" for e in got)


def test_events_long_poll_returns_on_new_event(live_server, tmp_path):
    """长轮询：wait 挂起期间 dispatch 新事件到达 → 立即返回（不等满 wait）。"""
    import json as _json
    import os as _os
    import threading
    import time as _time

    client, make = live_server
    task_dir = make(_sample_runs())

    # 先拉一次建立游标与桶状态（含 dispatch offset 首见初始化）。
    status, first = client.get(_url(task_dir, "/api/events"))
    assert status == 200
    cursor = max(e["seq"] for e in first)

    def _late_append():
        _time.sleep(0.6)
        log_dir = _os.path.join(task_dir, "总日志")
        with open(_os.path.join(log_dir, "dispatch.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(_json.dumps(
                {"seq": 99, "ts": "t", "run_id": "run-a", "event": "module.done",
                 "module": "m01"}, ensure_ascii=False) + "\n")

    threading.Thread(target=_late_append, daemon=True).start()
    t0 = _time.time()
    status, body = client.get(_url(task_dir, "/api/events", since=str(cursor), wait="10"))
    elapsed = _time.time() - t0
    assert status == 200
    assert elapsed < 5, f"长轮询未在事件到达后及时返回（耗时 {elapsed:.1f}s）"
    assert any(e["run_id"] == "run-a" for e in body)


def test_events_long_poll_timeout_returns_empty(live_server):
    """长轮询超时：wait 窗口内无事件 → 空数组返回（心跳语义）。"""
    import time as _time

    client, make = live_server
    task_dir = make(_sample_runs())
    status, first = client.get(_url(task_dir, "/api/events"))
    cursor = max(e["seq"] for e in first)
    t0 = _time.time()
    status, body = client.get(_url(task_dir, "/api/events", since=str(cursor), wait="1"))
    assert status == 200 and body == []
    assert _time.time() - t0 >= 0.9
