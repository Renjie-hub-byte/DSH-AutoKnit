"""fwapi.serve 数据桥 HTTP 端点测试（列表/详情/归档/空降级）。

验收对照：
1. GET /api/tasks 返回任务列表 JSON
2. /api/tasks 字段含 run_id/stage/stage_label/module_states/consumption 且按紧急度排序
3. /api/tasks/{run_id} 返回单 run 详情
4. POST /api/tasks/archive 写归档，GET /api/tasks/archived 返回已归档集合
5. 确定性空降级
"""
from urllib.parse import urlencode

import pytest

from conftest import make_archive, make_task_dir  # noqa: F401 (test/ 目录在 sys.path，无需相对导入)


def _sample_runs():
    return [
        {
            "run_id": "run-low",
            "stage": "planning",
            "task_name": "低紧急",
            "module_states": {"m01": {"status": "ok"}},
            "urgency": 1,
            "needs_human": False,
            "consumption": {"token_input": 10, "token_output": 5, "cache_hit": "no", "duration_sec": 2},
        },
        {
            "run_id": "run-high",
            "stage": "executor",
            "stage_label": "执行中",
            "task_name": "高紧急",
            "module_states": {"m01": {"status": "running"}, "m02": {"status": "pending"}},
            "urgency": 9,
            "needs_human": True,
            "consumption": {"token_input": 100, "token_output": 50, "cache_hit": "hit", "duration_sec": 12},
        },
        {
            "run_id": "run-mid",
            "stage": "auditor",
            "task_name": "中紧急",
            "module_states": {"m01": {"status": "done"}},
            "urgency": 5,
            "needs_human": False,
        },
    ]


def _url(task_dir, endpoint="/api/tasks"):
    return endpoint + "?" + urlencode({"task_dir": task_dir})


# ---------------------------------------------------------------- 列表 ----
def test_tasks_list_fields_and_sorting(tmp_path):
    """/api/tasks 字段齐全且按紧急度降序。"""
    task_dir = make_task_dir(tmp_path, _sample_runs())
    # 直接用数据源层验证契约字段
    from fwapi.dsh import task as ts

    items = ts.list_tasks(task_dir)
    assert [it["run_id"] for it in items] == ["run-high", "run-mid", "run-low"]

    high = items[0]
    for key in ("run_id", "stage", "stage_label", "module_states", "urgency", "needs_human", "consumption"):
        assert key in high, key
    assert high["consumption"] == {
        "token_input": 100,
        "token_output": 50,
        "cache_hit": "hit",
        "duration_sec": 12,
    }
    # 缺 stage_label 的 run 用枚举映射兜底
    assert items[1]["stage_label"] == "审核中"


def test_tasks_list_http(live_server):
    """验收1+2：HTTP 层 GET /api/tasks 返回 JSON 列表且按紧急度排序。"""
    client, make = live_server
    task_dir = make(_sample_runs())
    status, body = client.get(_url(task_dir))
    assert status == 200
    assert isinstance(body, list) and len(body) == 3
    assert [it["run_id"] for it in body] == ["run-high", "run-mid", "run-low"]
    first = body[0]
    for key in ("run_id", "stage", "stage_label", "module_states", "consumption"):
        assert key in first


def test_tasks_list_excludes_archived(live_server, tmp_path):
    """归档后列表不展示：/api/tasks 过滤已归档 run_id。"""
    client, make = live_server
    task_dir = make(_sample_runs())
    make_archive(tmp_path, task_dir, ["run-high"])
    status, body = client.get(_url(task_dir))
    assert status == 200
    run_ids = [it["run_id"] for it in body]
    assert "run-high" not in run_ids
    assert set(run_ids) == {"run-mid", "run-low"}


def test_tasks_list_empty_when_dir_missing(live_server):
    """验收5：目录缺失确定性空降级 tasks=[]。"""
    client, _ = live_server
    status, body = client.get(_url("/no/such/dir"))
    assert status == 200
    assert body == []


def test_tasks_list_empty_when_no_runs_file(live_server, tmp_path):
    """目录存在但无 runs.json：确定性空降级 []。"""
    client, _ = live_server
    task_dir = tmp_path / "empty-task"
    task_dir.mkdir()
    status, body = client.get(_url(str(task_dir)))
    assert status == 200 and body == []


# ---------------------------------------------------------------- 详情 ----
def test_tasks_detail_http(live_server):
    """验收3：/api/tasks/{run_id} 返回单 run 详情。"""
    client, make = live_server
    task_dir = make(_sample_runs())
    status, body = client.get(_url(task_dir, f"/api/tasks/run-high"))
    assert status == 200
    assert isinstance(body, dict)
    for key in ("run_id", "stage", "stage_label", "task_name", "module_states"):
        assert key in body, key
    assert body["run_id"] == "run-high"
    assert body["task_name"] == "高紧急"
    assert body["module_states"]["m02"]["status"] == "pending"
    assert body["needs_human"] is True


def test_tasks_detail_miss_returns_null(live_server):
    """run_id 未命中确定性空降级 null。"""
    client, make = live_server
    task_dir = make(_sample_runs())
    status, body = client.get(_url(task_dir, "/api/tasks/nope"))
    assert status == 200
    assert body is None


def test_tasks_detail_invalid_dir_returns_null(live_server):
    """目录无效确定性空降级 null。"""
    client, _ = live_server
    status, body = client.get(_url("/no/such/dir", "/api/tasks/run-high"))
    assert status == 200 and body is None


# ---------------------------------------------------------------- 归档 ----
def test_archive_and_archived_http(live_server):
    """验收4：POST archive 写归档，GET archived 读到集合。"""
    client, make = live_server
    task_dir = make(_sample_runs())

    status, body = client.get(_url(task_dir, "/api/tasks/archived"))
    assert status == 200 and body == []

    status, body = client.post(
        "/api/tasks/archive", {"task_dir": task_dir, "run_id": "run-mid"}
    )
    assert status == 200
    assert body == {"ok": True, "run_id": "run-mid", "archived": True}

    status, body = client.get(_url(task_dir, "/api/tasks/archived"))
    assert status == 200 and body == ["run-mid"]


def test_archive_idempotent(live_server, tmp_path):
    """重复归档幂等：不重复、仍成功。"""
    client, make = live_server
    task_dir = make(_sample_runs())
    for _ in range(3):
        client.post("/api/tasks/archive", {"task_dir": task_dir, "run_id": "run-low"})
    status, body = client.get(_url(task_dir, "/api/tasks/archived"))
    assert body == ["run-low"]


def test_archived_missing_file_returns_empty(live_server):
    """归档文件缺失返回空数组。"""
    client, _ = live_server
    status, body = client.get(_url("/no/such/dir", "/api/tasks/archived"))
    assert status == 200 and body == []


def test_archive_empty_run_id(live_server):
    """空 run_id 返回 ok=False。"""
    client, make = live_server
    task_dir = make(_sample_runs())
    status, body = client.post("/api/tasks/archive", {"task_dir": task_dir, "run_id": ""})
    assert status == 400
    assert body["ok"] is False
