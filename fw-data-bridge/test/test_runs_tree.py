"""真实快照数据源测试：dsh.task.list_runs / get_run_tree + GET /api/runs、/api/runs/{id}/tree。

验收对照（first_block acceptance）：
1. GET /api/runs 从真实快照聚合 run 列表，目录缺失确定性返回 []，对应单测绿
2. GET /api/runs/{id}/tree 返回 modules + dependencies + per_module 全字段 + split 子树 + needs_human 标记，未命中返 null
3. 旧 /api/tasks* 端点仍可用，无回归
4. serve.py 与 dsh/task.py 的 pytest 新端点单测全绿
"""
from urllib.parse import urlencode

from conftest import make_snapshot  # noqa: F401


def _sample_snapshot(needs_human=None, run_id="run-sample-001"):
    """构造一个含 split 子树（m03→m03a）与 needs_human 标记的真实快照。"""
    return {
        "schema_version": 4,
        "run_id": run_id,
        "task": "autoknit-v2-样例",
        "updated_at": "2026-08-26T23:11:42+08:00",
        "status": "complete",
        "cause": "all_modules_done",
        "modules": {"m01": "done", "m02": "done", "m03": "done", "m03a": "done"},
        "dependencies": {"m01": [], "m02": [], "m03": ["m01"], "m03a": ["m01"]},
        "per_module": {
            "m01": {
                "executor_round": 2,
                "auditor_round": 2,
                "executor_id": "E1",
                "last_verdict": "pass",
                "reason": "全绿",
                "split_depth": 0,
                "parent_module": "",
                "child_modules": [],
                "tokens_used": 0,
                "started_at": "2026-08-26T22:25:42+08:00",
                "ended_at": "2026-08-26T22:40:08+08:00",
            },
            "m03": {
                "executor_round": 1,
                "auditor_round": 1,
                "executor_id": "E1",
                "last_verdict": "pass",
                "reason": "分裂出 m03a",
                "split_depth": 1,
                "parent_module": "",
                "child_modules": ["m03a"],
                "tokens_used": 5,
                "started_at": "2026-08-26T22:47:01+08:00",
                "ended_at": "2026-08-26T23:11:42+08:00",
            },
            "m03a": {
                "executor_round": 2,
                "auditor_round": 2,
                "executor_id": "E1",
                "last_verdict": "pass",
                "reason": "子模块收官",
                "split_depth": 1,
                "parent_module": "m03",
                "child_modules": [],
                "tokens_used": 3,
                "started_at": "2026-08-26T22:59:27+08:00",
                "ended_at": "2026-08-26T23:11:42+08:00",
            },
        },
        "needs_human": needs_human if needs_human is not None else [],
    }


def _url(task_dir, endpoint):
    return endpoint + "?" + urlencode({"task_dir": task_dir})


# ------------------------------------------------------------ 数据源层 runs ----
def test_list_runs_data_source(tmp_path):
    """list_runs 从真实快照聚合出契约字段齐全的 run item。"""
    from fwapi.dsh import task as ts

    task_dir = make_snapshot(tmp_path, _sample_snapshot())
    items = ts.list_runs(task_dir)
    assert len(items) == 1
    it = items[0]
    for key in ("run_id", "task", "status", "stage", "cause", "updated_at", "needs_human_modules"):
        assert key in it, key
    assert it["run_id"] == "run-sample-001"
    assert it["task"] == "autoknit-v2-样例"
    assert it["status"] == "complete"
    assert it["cause"] == "all_modules_done"
    assert it["needs_human_modules"] == []


def test_list_runs_needs_human_modules(tmp_path):
    """needs_human 非空时：runs item 带模块清单，stage 落到 needs_human。"""
    from fwapi.dsh import task as ts

    snap = _sample_snapshot(needs_human=["m03"])
    task_dir = make_snapshot(tmp_path, snap)
    it = ts.list_runs(task_dir)[0]
    assert it["needs_human_modules"] == ["m03"]
    assert it["stage"] == "needs_human"


def test_list_runs_empty_when_dir_missing(tmp_path):
    """目录缺失确定性空降级 []。"""
    from fwapi.dsh import task as ts

    assert ts.list_runs(str(tmp_path / "no-such-dir")) == []


def test_list_runs_empty_when_snapshot_missing(tmp_path):
    """目录存在但无 快照.json → 确定性空降级 []。"""
    from fwapi.dsh import task as ts

    empty = tmp_path / "empty-task"
    empty.mkdir()
    assert ts.list_runs(str(empty)) == []


# ------------------------------------------------------------ 数据源层 tree ----
def test_tree_data_source(tmp_path):
    """get_run_tree 返回 modules + dependencies + per_module 全字段 + split 子树 + needs_human。"""
    from fwapi.dsh import task as ts

    task_dir = make_snapshot(tmp_path, _sample_snapshot())
    tree = ts.get_run_tree(task_dir, "run-sample-001")
    assert tree is not None
    assert tree["run_id"] == "run-sample-001"
    # modules 为对象数组：顶层 root（m01/m02/m03），m03a 挂在 m03.split 下（契约对齐面板）
    assert sorted(m["id"] for m in tree["modules"]) == ["m01", "m02", "m03"]
    m3 = next(m for m in tree["modules"] if m["id"] == "m03")
    assert m3["status"] == "done"
    assert m3["dependencies"] == ["m01"]
    assert m3["token_used"] == 5
    assert [c["id"] for c in m3["split"]] == ["m03a"]
    assert tree["dependencies"]["m03"] == ["m01"]

    # per_module 全字段
    pm = tree["per_module"]["m03"]
    for key in (
        "executor_round",
        "auditor_round",
        "executor_id",
        "last_verdict",
        "reason",
        "split_depth",
        "parent_module",
        "child_modules",
        "tokens_used",
        "started_at",
        "ended_at",
    ):
        assert key in pm, key

    # split 子树：parent/child 双向链 + 深度
    assert pm["split_depth"] == 1
    assert pm["child_modules"] == ["m03a"]
    assert tree["per_module"]["m03a"]["parent_module"] == "m03"
    assert tree["per_module"]["m03a"]["split_depth"] == 1

    assert tree["needs_human"] == []


def test_tree_per_module_defaults(tmp_path):
    """per_module 契约字段缺失时用默认补齐，不抛异常。"""
    from fwapi.dsh import task as ts

    snap = _sample_snapshot()
    snap["per_module"]["m02"] = {"executor_id": "E2"}  # 只给一个字段
    task_dir = make_snapshot(tmp_path, snap)
    pm = ts.get_run_tree(task_dir, "run-sample-001")["per_module"]["m02"]
    assert pm["executor_id"] == "E2"
    assert pm["executor_round"] == 0
    assert pm["split_depth"] == 0
    assert pm["child_modules"] == []
    assert pm["reason"] == ""


def test_tree_miss_returns_none(tmp_path):
    """run_id 未命中确定性返回 None。"""
    from fwapi.dsh import task as ts

    task_dir = make_snapshot(tmp_path, _sample_snapshot())
    assert ts.get_run_tree(task_dir, "run-nope") is None


def test_tree_invalid_dir_returns_none(tmp_path):
    """目录无效确定性返回 None。"""
    from fwapi.dsh import task as ts

    assert ts.get_run_tree(str(tmp_path / "no-such-dir"), "run-sample-001") is None


# ---------------------------------------------------------------- HTTP 层 ----
def test_runs_http(live_server, tmp_path):
    """验收1：GET /api/runs 从真实快照聚合 run 列表。"""
    client, _ = live_server
    task_dir = make_snapshot(tmp_path, _sample_snapshot())
    status, body = client.get(_url(task_dir, "/api/runs"))
    assert status == 200
    assert isinstance(body, list) and len(body) == 1
    for key in ("run_id", "task", "status", "stage", "cause", "updated_at", "needs_human_modules"):
        assert key in body[0], key


def test_runs_http_empty_when_dir_missing(live_server):
    """验收1：目录缺失 GET /api/runs 确定性返回 []，HTTP 200。"""
    client, _ = live_server
    status, body = client.get(_url("/no/such/dir", "/api/runs"))
    assert status == 200 and body == []


def test_run_tree_http(live_server, tmp_path):
    """验收2：GET /api/runs/{id}/tree 返回完整执行树。"""
    client, _ = live_server
    task_dir = make_snapshot(tmp_path, _sample_snapshot())
    status, body = client.get(_url(task_dir, "/api/runs/run-sample-001/tree"))
    assert status == 200
    assert isinstance(body, dict)
    assert sorted(m["id"] for m in body["modules"]) == ["m01", "m02", "m03"]
    assert next(m for m in body["modules"] if m["id"] == "m03")["split"][0]["id"] == "m03a"
    assert "dependencies" in body
    assert "per_module" in body
    assert "needs_human" in body
    assert body["per_module"]["m03a"]["parent_module"] == "m03"


def test_run_tree_http_miss_returns_null(live_server, tmp_path):
    """验收2：未命中 GET /api/runs/{id}/tree 确定性返回 JSON null。"""
    client, _ = live_server
    task_dir = make_snapshot(tmp_path, _sample_snapshot())
    status, body = client.get(_url(task_dir, "/api/runs/nope/tree"))
    assert status == 200 and body is None


def test_run_tree_http_invalid_dir_null(live_server):
    """目录无效 tree 确定性返回 null。"""
    client, _ = live_server
    status, body = client.get(_url("/no/such/dir", "/api/runs/run-sample-001/tree"))
    assert status == 200 and body is None


def test_runs_post_not_allowed(live_server):
    """runs 命名空间仅 GET：POST 回落 404 not_found。"""
    client, _ = live_server
    status, body = client.post("/api/runs", {})
    assert status == 404
    assert body["error"] == "not_found"


# ------------------------------------------------------- 旧端点无回归 ----
def test_old_tasks_endpoints_no_regression(live_server, tmp_path):
    """验收3：旧 /api/tasks* 在引入 runs 命名空间后仍可用。"""
    from conftest import make_task_dir

    client, _ = live_server
    runs = [
        {
            "run_id": "run-mock",
            "stage": "executor",
            "task_name": "旧端点",
            "module_states": {"m01": {"status": "ok"}},
            "urgency": 3,
            "needs_human": False,
            "consumption": {"token_input": 1, "token_output": 1, "cache_hit": "no", "duration_sec": 1},
        }
    ]
    task_dir = make_task_dir(tmp_path, runs)
    status, body = client.get(_url(task_dir, "/api/tasks"))
    assert status == 200
    assert body[0]["run_id"] == "run-mock"

    status, body = client.get(_url(task_dir, "/api/tasks/run-mock"))
    assert status == 200
    assert body["task_name"] == "旧端点"
