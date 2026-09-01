"""m01 首发块新增测试：registry.py 注册表读写 + task.py 注册表聚合（list_runs 多 run / get_run_detail）。

验收对照（first_block acceptance，唯一权威）：
1. registry.py 读写契约（字段/枚举/ts 格式）落盘正确
2. /api/runs 聚合多 run 排序正确（active 优先、updated_at 降序）
3. /api/runs/{id} 按注册表定位 task_dir 取详情，未命中 null
4. 注册表缺失确定性回落单 task_dir（现有单快照行为不破坏）
5. 新增 pytest 绿 + 既有 pytest 全量无回归
"""
import json
import os

from conftest import make_registry, make_snapshot  # noqa: F401

# 契约注册表 record 字段（与 data_contract runs_registry columns 对齐）。
_RECORD_FIELDS = ("run_id", "task_dir", "task", "status", "started_at", "updated_at")


def _snapshot(run_id, task="t", status="complete", needs_human=None, updated_at="2026-08-29T00:00:00+00:00"):
    """构造真实快照（与 make_snapshot 配合，写 总日志/快照.json）。"""
    return {
        "schema_version": 4,
        "run_id": run_id,
        "task": task,
        "status": status,
        "cause": "done",
        "updated_at": updated_at,
        "modules": {"m01": "done"},
        "dependencies": {"m01": []},
        "per_module": {"m01": {"executor_round": 1}},
        "needs_human": needs_human if needs_human is not None else [],
    }


def _registry_path_env(monkeypatch, tmp_path, records):
    """写注册表文件并让环境变量指向它；返回注册表路径。"""
    path = make_registry(tmp_path, records)
    monkeypatch.setenv("AUTOKNIT_RUNS_REGISTRY", path)
    return path


# ======================================================== registry 读写契约 ----
def test_registry_roundtrip_fields_enum_ts(monkeypatch, tmp_path):
    """验收1：registry 落盘/读回字段、status 枚举、ISO-8601 UTC ts 格式正确。"""
    from fwapi import registry as reg

    path = _registry_path_env(monkeypatch, tmp_path, [])
    ok = reg.upsert_record({
        "run_id": "run-r1",
        "task_dir": "/tmp/task-a",
        "task": "样例任务",
        "status": "active",
        "started_at": "2026-08-29T00:00:00+00:00",
        "updated_at": "2026-08-29T01:00:00+00:00",
    })
    assert ok is not None

    # 落盘字段齐全且为契约枚举/ts 格式
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    rec = payload["runs"][0]
    assert set(rec.keys()) == set(_RECORD_FIELDS)
    assert rec["status"] in ("active", "complete", "archived")
    assert "T" in rec["started_at"] and "+00:00" in rec["started_at"]

    # 读回一致（归一化 record）
    recs = reg.read_records()
    assert len(recs) == 1
    assert recs[0]["run_id"] == "run-r1"
    assert recs[0]["task_dir"] == "/tmp/task-a"


def test_registry_normalize_unknown_status(monkeypatch, tmp_path):
    """验收1：未知 status 确定性标 unknown；缺 run_id 记录被丢弃。"""
    from fwapi import registry as reg

    path = _registry_path_env(monkeypatch, tmp_path, [
        {"run_id": "r-bad-status", "task_dir": "/x", "task": "x", "status": "paused"},
        {"task_dir": "/y"},  # 缺 run_id → 丢弃
        {"run_id": "r-ok", "task_dir": "/z", "task": "ok", "status": "active"},
    ])
    recs = reg.read_records()
    assert [r["run_id"] for r in recs] == ["r-bad-status", "r-ok"]
    assert recs[0]["status"] == "unknown"


def test_registry_missing_and_corrupt(monkeypatch, tmp_path):
    """验收1：文件缺失/损坏确定性读为 []，不抛异常。"""
    from fwapi import registry as reg

    # 缺失
    monkeypatch.setenv("AUTOKNIT_RUNS_REGISTRY", str(tmp_path / "no-reg" / "runs.json"))
    assert reg.read_records() == []
    assert reg.has_records() is False

    # 损坏
    path = tmp_path / "corrupt.json"
    path.write_text("not json{{{", encoding="utf-8")
    monkeypatch.setenv("AUTOKNIT_RUNS_REGISTRY", str(path))
    assert reg.read_records() == []


def test_registry_default_path_resolution(monkeypatch):
    """验收1：未设环境变量时默认路径为 ~/.autoknit/runs.json。"""
    from fwapi import registry as reg

    monkeypatch.delenv("AUTOKNIT_RUNS_REGISTRY", raising=False)
    import os as _os
    assert reg.resolve_registry_path() == _os.path.join(_os.path.expanduser("~"), ".autoknit", "runs.json")


def test_registry_set_status_archive(monkeypatch, tmp_path):
    """验收1：set_status 幂等更新状态并刷新 updated_at。"""
    from fwapi import registry as reg

    _registry_path_env(monkeypatch, tmp_path, [
        {"run_id": "run-a", "task_dir": "/x", "task": "t", "status": "active",
         "started_at": "2026-08-29T00:00:00+00:00", "updated_at": "2026-08-29T00:00:00+00:00"},
    ])
    updated = reg.set_status("run-a", "archived")
    assert updated is not None and updated["status"] == "archived"
    rec = reg.get_record("run-a")
    assert rec["status"] == "archived"
    # 未命中 → None
    assert reg.set_status("nope", "archived") is None


# ======================================================== list_runs 聚合 ----
def _two_run_registry(tmp_path, monkeypatch, records):
    """写注册表 + 为每个 record 建快照目录，返回记录列表（含 task_dir）。"""
    runs = []
    for i, rec in enumerate(records):
        task_dir = make_snapshot(tmp_path, _snapshot(
            rec["run_id"], task=rec.get("task", f"task-{i}"),
            status=rec.get("status", "complete"),
            needs_human=rec.get("needs_human"),
            updated_at=rec.get("snap_updated", "2026-08-29T00:00:00+00:00"),
        ))
        runs.append({**rec, "task_dir": task_dir})
    _registry_path_env(monkeypatch, tmp_path, runs)
    return runs


def test_list_runs_aggregate_active_first_updated_desc(tmp_path, monkeypatch):
    """验收2：/api/runs 聚合多 run，active 优先、updated_at 降序。"""
    from fwapi.dsh import task as ts

    _two_run_registry(tmp_path, monkeypatch, [
        {"run_id": "r-complete-old", "status": "complete", "snap_updated": "2026-08-28T00:00:00+00:00"},
        {"run_id": "r-active-new", "status": "active", "snap_updated": "2026-08-29T02:00:00+00:00"},
        {"run_id": "r-active-old", "status": "active", "snap_updated": "2026-08-29T01:00:00+00:00"},
    ])
    items = ts.list_runs()
    assert [it["run_id"] for it in items] == ["r-active-new", "r-active-old", "r-complete-old"]

    # 每个 item 契约字段齐全
    it = items[0]
    for key in ("run_id", "task", "task_dir", "status", "stage", "updated_at", "started_at", "needs_human_modules"):
        assert key in it, key
    assert it["status"] == "active"
    assert it["task_dir"]  # 来自注册表定位的各自 task_dir


def test_list_runs_detail_from_each_task_dir(tmp_path, monkeypatch):
    """验收2：每 run 从各自 task_dir 快照取详情（needs_human 来自对应快照）。"""
    from fwapi.dsh import task as ts

    _two_run_registry(tmp_path, monkeypatch, [
        {"run_id": "r-a", "status": "active", "needs_human": ["m01"]},
        {"run_id": "r-b", "status": "active", "needs_human": []},
    ])
    items = ts.list_runs()
    by_id = {it["run_id"]: it for it in items}
    assert by_id["r-a"]["needs_human_modules"] == ["m01"]
    assert by_id["r-a"]["stage"] == "needs_human"
    assert by_id["r-b"]["needs_human_modules"] == []


def test_list_runs_fallback_single_task_dir(tmp_path, monkeypatch):
    """验收4：注册表缺失确定性回落单 task_dir（现有单快照行为不破坏）。"""
    from fwapi.dsh import task as ts

    # 环境变量指向不存在路径（autouse 隔离）
    task_dir = make_snapshot(tmp_path, _snapshot("run-single", status="complete"))
    items = ts.list_runs(task_dir)
    assert len(items) == 1
    assert items[0]["run_id"] == "run-single"
    assert items[0]["status"] == "complete"


def test_list_runs_http_aggregate(live_server, tmp_path, monkeypatch):
    """验收2：HTTP 层 GET /api/runs 聚合多 run 排序正确。"""
    client, _ = live_server
    _two_run_registry(tmp_path, monkeypatch, [
        {"run_id": "r-active", "status": "active", "snap_updated": "2026-08-29T03:00:00+00:00"},
        {"run_id": "r-complete", "status": "complete", "snap_updated": "2026-08-29T02:00:00+00:00"},
    ])
    status, body = client.get("/api/runs")
    assert status == 200
    assert [it["run_id"] for it in body] == ["r-active", "r-complete"]


def test_list_runs_http_fallback(live_server, tmp_path):
    """验收4：注册表缺失 GET /api/runs 回落单 task_dir 快照。"""
    client, _ = live_server
    task_dir = make_snapshot(tmp_path, _snapshot("run-single"))
    status, body = client.get(f"/api/runs?task_dir={task_dir}")
    assert status == 200
    assert len(body) == 1 and body[0]["run_id"] == "run-single"


# ======================================================== get_run_detail ----
def test_run_detail_from_registry_task_dir(tmp_path, monkeypatch):
    """验收3：按注册表定位 task_dir 取详情，含 cause/task_dir/started_at。"""
    from fwapi.dsh import task as ts

    _two_run_registry(tmp_path, monkeypatch, [
        {"run_id": "r-a", "status": "active", "snap_updated": "2026-08-29T02:00:00+00:00"},
    ])
    rec = ts.get_run_detail("", "r-a")
    assert rec is not None
    assert rec["run_id"] == "r-a"
    assert rec["task_dir"]
    assert "cause" in rec and "started_at" in rec and "needs_human_modules" in rec


def test_run_detail_miss_returns_null(tmp_path, monkeypatch):
    """验收3：注册表存在但不含该 run_id → 确定性 null。"""
    from fwapi.dsh import task as ts

    _two_run_registry(tmp_path, monkeypatch, [
        {"run_id": "r-a", "status": "active"},
    ])
    assert ts.get_run_detail("", "r-nope") is None


def test_run_detail_empty_run_id(tmp_path):
    """空 run_id → null。"""
    from fwapi.dsh import task as ts

    assert ts.get_run_detail("", "") is None


def test_run_detail_fallback_single_task_dir(tmp_path, monkeypatch):
    """验收4：注册表缺失回落单 task_dir 快照取详情。"""
    from fwapi.dsh import task as ts

    task_dir = make_snapshot(tmp_path, _snapshot("run-single", status="complete"))
    rec = ts.get_run_detail(task_dir, "run-single")
    assert rec is not None and rec["run_id"] == "run-single"


def test_run_detail_http(live_server, tmp_path, monkeypatch):
    """验收3：HTTP 层 GET /api/runs/{id} 取详情；未命中 null。"""
    client, _ = live_server
    _two_run_registry(tmp_path, monkeypatch, [
        {"run_id": "r-a", "status": "active"},
    ])
    status, body = client.get("/api/runs/r-a")
    assert status == 200
    assert isinstance(body, dict)
    assert body["run_id"] == "r-a"
    assert "cause" in body and "task_dir" in body and "started_at" in body

    status, body = client.get("/api/runs/r-nope")
    assert status == 200 and body is None
