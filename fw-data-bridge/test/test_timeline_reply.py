"""m01 数据桥新增端点测试：GET /api/runs/{id}/timeline 与 POST /api/runs/{id}/reply。

验收对照（first_block acceptance）：
1. GET /api/runs/{id}/timeline 返回 dispatch.jsonl 事件流按 seq 升序，事件枚举对齐契约，
   缺失确定性返 []，单测绿
2. POST /api/runs/{id}/reply 白名单校验 continue/retry/revise/自定义（自定义必填 instruction），
   成功写 needs_human/reply.md、失败确定性 JSON（错误信封），单测绿
3. timeline 与 dispatch.jsonl 逐条对拍一致；reply 端到端实测选 continue 写 reply.md 成功
   且内容/命令正确
4. 旧 /api/runs、/api/runs/{id}/tree 与 /api/tasks* 测试全绿无回归
"""
import json
import os

from conftest import make_snapshot  # noqa: F401

# 与 dsh/task.py TIMELINE_EVENTS 对齐的独立契约枚举（供对拍/校验，避免直接 import 内部常量）。
_EVENTS = (
    "run.start",
    "module.dispatch",
    "executor.round.start",
    "executor.round.done",
    "auditor.round.start",
    "auditor.round",
    "module.split",
    "module.aggregated",
    "module.final_block",
    "module.done",
    "integration.check",
)


def _snapshot(run_id="run-a", needs_human=None):
    """构造一个真实快照（可选 needs_human），供 reply/timeline HTTP 与数据源测试使用。"""
    return {
        "schema_version": 4,
        "run_id": run_id,
        "task": "t",
        "status": "running",
        "cause": "",
        "updated_at": "2026-08-29T00:00:00+08:00",
        "modules": {"m01": "running"},
        "dependencies": {"m01": []},
        "per_module": {"m01": {"executor_round": 1, "auditor_round": 0}},
        "needs_human": needs_human if needs_human is not None else ["m01"],
    }


def _make_dispatch_task(tmp_path, run_id, lines):
    """构造任务目录（总日志/dispatch.jsonl + 快照.json），返回绝对路径。"""
    task_dir = make_snapshot(tmp_path, _snapshot(run_id))
    log_dir = os.path.join(task_dir, "总日志")
    with open(os.path.join(log_dir, "dispatch.jsonl"), "w", encoding="utf-8") as fh:
        for rec in lines:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return task_dir


def _dispatch_lines(run_id="run-a"):
    """构造一段乱序 dispatch 事件流（含应被过滤的无 seq/未知事件/异 run 事件）。"""
    return [
        {"seq": 3, "ts": "T3", "run_id": run_id, "event": "executor.round.start", "module": "m01", "detail": {"round": 1}},
        {"seq": 1, "ts": "T1", "run_id": run_id, "event": "run.start", "module": None, "detail": {"task": "t"}},
        {"ts": "T0", "run_id": run_id, "event": "scaffold", "detail": {}},  # 无 seq + 非枚举 → 过滤
        {"seq": 2, "ts": "T2", "run_id": run_id, "event": "module.dispatch", "module": "m01", "detail": {"executor_id": "E1"}},
        {"seq": 4, "ts": "T4", "run_id": run_id, "event": "unknown.event", "module": "m01", "detail": {}},  # 枚举外 → 过滤
        {"seq": 5, "ts": "T5", "run_id": "other-run", "event": "run.start", "module": None, "detail": {}},  # 异 run → 过滤
    ]


# ============================================================== timeline 数据源 ----
def test_timeline_sorted_enum_only(tmp_path):
    """/api/runs/{id}/timeline 数据源：按 seq 升序、只保留契约枚举事件、run_id 匹配。"""
    from fwapi.dsh import task as ts

    task_dir = _make_dispatch_task(tmp_path, "run-a", _dispatch_lines())
    events = ts.get_run_timeline(task_dir, "run-a")
    assert [e["seq"] for e in events] == [1, 2, 3]
    assert events[0] == {"seq": 1, "ts": "T1", "event": "run.start", "module": "", "detail": {"task": "t"}}
    assert events[1]["event"] == "module.dispatch"
    assert events[2]["event"] == "executor.round.start"
    assert all(e["event"] in _EVENTS for e in events)


def test_timeline_empty_run_id(tmp_path):
    """run_id 为空 → 确定性 []。"""
    from fwapi.dsh import task as ts

    task_dir = _make_dispatch_task(tmp_path, "run-a", _dispatch_lines())
    assert ts.get_run_timeline(task_dir, "") == []


def test_timeline_empty_when_dir_missing(tmp_path):
    """目录缺失 → 确定性 []。"""
    from fwapi.dsh import task as ts

    assert ts.get_run_timeline(str(tmp_path / "no-such"), "run-a") == []


def test_timeline_empty_when_file_missing(tmp_path):
    """目录存在但无 dispatch.jsonl → 确定性 []。"""
    from fwapi.dsh import task as ts

    task_dir = make_snapshot(tmp_path, _snapshot("run-a"))
    assert ts.get_run_timeline(task_dir, "run-a") == []


# ================================================= timeline 与 dispatch 对拍 ----
def test_timeline_matches_dispatch_exactly(tmp_path):
    """对拍：get_run_timeline 输出与直接读 dispatch.jsonl 独立重放的逐条结果完全一致。"""
    from fwapi.dsh import task as ts

    task_dir = _make_dispatch_task(tmp_path, "run-a", _dispatch_lines())

    # 独立重放：按同一确定性规则从文件推导期望
    expected = []
    with open(os.path.join(task_dir, "总日志", "dispatch.jsonl"), encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("run_id") != "run-a":
                continue
            ev = rec.get("event")
            if ev not in _EVENTS:
                continue
            try:
                seq = int(rec.get("seq"))
            except (TypeError, ValueError):
                continue
            expected.append(
                {
                    "seq": seq,
                    "ts": rec.get("ts") if isinstance(rec.get("ts"), str) else "",
                    "event": ev,
                    "module": rec.get("module") if isinstance(rec.get("module"), str) else "",
                    "detail": rec.get("detail") if isinstance(rec.get("detail"), dict) else {},
                }
            )
    expected.sort(key=lambda e: e["seq"])

    got = ts.get_run_timeline(task_dir, "run-a")
    assert got == expected
    assert [e["seq"] for e in got] == sorted(e["seq"] for e in expected)


# ============================================================== timeline HTTP ----
def test_timeline_http(live_server, tmp_path):
    """验收1：GET /api/runs/{id}/timeline 返回按 seq 升序的事件流。"""
    client, _ = live_server
    task_dir = _make_dispatch_task(tmp_path, "run-a", _dispatch_lines())
    status, body = client.get(f"/api/runs/run-a/timeline?task_dir={task_dir}")
    assert status == 200
    assert isinstance(body, list)
    assert [e["seq"] for e in body] == [1, 2, 3]
    for key in ("seq", "ts", "event", "module", "detail"):
        assert key in body[0], key


def test_timeline_http_empty_when_dir_missing(live_server):
    """验收1：目录缺失 GET timeline 确定性返回 []，HTTP 200。"""
    client, _ = live_server
    status, body = client.get("/api/runs/run-a/timeline?task_dir=/no/such/dir")
    assert status == 200 and body == []


def test_timeline_post_not_allowed(live_server):
    """timeline 仅 GET：POST 回落 404。"""
    client, _ = live_server
    status, body = client.post("/api/runs/run-a/timeline", {"task_dir": "/x"})
    assert status == 404 and body["error"] == "not_found"


# ============================================================== reply 数据源 ----
def test_reply_continue_writes_file(tmp_path):
    """验收2+3：continue 成功写 needs_human/reply.md，内容/命令正确。"""
    from fwapi.dsh import reply as rp

    task_dir = make_snapshot(tmp_path, _snapshot("run-a"))
    res = rp.reply(task_dir, "run-a", {"command": "continue"})
    assert res["success"] is True
    path = os.path.join(task_dir, "needs_human", "reply.md")
    assert os.path.isfile(path)
    content = open(path, encoding="utf-8").read()
    assert "command: continue" in content
    assert "run_id: run-a" in content


def test_reply_custom_requires_instruction(tmp_path):
    """验收2：自定义命令必填 instruction。"""
    from fwapi.dsh import reply as rp

    task_dir = make_snapshot(tmp_path, _snapshot("run-a"))
    res = rp.reply(task_dir, "run-a", {"command": "自定义"})
    assert res["success"] is False
    assert "instruction" in res["detail"]


def test_reply_custom_with_instruction(tmp_path):
    """自定义 + instruction → 成功且内容写入 instruction。"""
    from fwapi.dsh import reply as rp

    task_dir = make_snapshot(tmp_path, _snapshot("run-a"))
    res = rp.reply(task_dir, "run-a", {"command": "自定义", "instruction": "改签名"})
    assert res["success"] is True
    content = open(os.path.join(task_dir, "needs_human", "reply.md"), encoding="utf-8").read()
    assert "改签名" in content


def test_reply_invalid_command(tmp_path):
    """验收2：非白名单 command → 失败确定性 JSON。"""
    from fwapi.dsh import reply as rp

    task_dir = make_snapshot(tmp_path, _snapshot("run-a"))
    res = rp.reply(task_dir, "run-a", {"command": "go"})
    assert res["success"] is False
    assert "白名单" in res["detail"]


def test_reply_empty_run_id(tmp_path):
    """空 run_id → 失败确定性 JSON。"""
    from fwapi.dsh import reply as rp

    task_dir = make_snapshot(tmp_path, _snapshot("run-a"))
    res = rp.reply(task_dir, "", {"command": "continue"})
    assert res["success"] is False
    assert "run_id" in res["detail"]


def test_reply_not_pending(tmp_path):
    """run 当前不需要人工决策 → 失败（非 needs_human 错误信封）。"""
    from fwapi.dsh import reply as rp

    task_dir = make_snapshot(tmp_path, _snapshot("run-a", needs_human=[]))
    res = rp.reply(task_dir, "run-a", {"command": "continue"})
    assert res["success"] is False
    assert "不需要人工决策" in res["detail"]


def test_reply_run_miss(tmp_path):
    """run 未命中 / 目录无效 → 失败。"""
    from fwapi.dsh import reply as rp

    task_dir = make_snapshot(tmp_path, _snapshot("run-a"))
    assert rp.reply(task_dir, "nope", {"command": "continue"})["success"] is False
    assert rp.reply("/no/such/dir", "run-a", {"command": "continue"})["success"] is False


# ============================================================== reply HTTP ----
def test_reply_http_e2e_continue(live_server, tmp_path):
    """验收2+3：POST reply=continue 端到端写 reply.md，成功且命令正确。"""
    client, _ = live_server
    task_dir = make_snapshot(tmp_path, _snapshot("run-a"))
    status, body = client.post(
        "/api/runs/run-a/reply", {"task_dir": task_dir, "command": "continue"}
    )
    assert status == 200
    assert body["success"] is True
    assert isinstance(body["detail"], str)
    content = open(os.path.join(task_dir, "needs_human", "reply.md"), encoding="utf-8").read()
    assert "command: continue" in content
    assert "run_id: run-a" in content


def test_reply_http_invalid_command(live_server, tmp_path):
    """验收2：非法 command → 400 且 success=False 确定性 JSON。"""
    client, _ = live_server
    task_dir = make_snapshot(tmp_path, _snapshot("run-a"))
    status, body = client.post(
        "/api/runs/run-a/reply", {"task_dir": task_dir, "command": "go"}
    )
    assert status == 400
    assert body["success"] is False
    assert isinstance(body["detail"], str)


def test_reply_http_custom_requires_instruction(live_server, tmp_path):
    """自定义必填 instruction（HTTP 层）。"""
    client, _ = live_server
    task_dir = make_snapshot(tmp_path, _snapshot("run-a"))
    status, body = client.post(
        "/api/runs/run-a/reply", {"task_dir": task_dir, "command": "自定义"}
    )
    assert status == 400 and body["success"] is False


def test_reply_get_not_allowed(live_server):
    """reply 仅 POST：GET 回落 404。"""
    client, _ = live_server
    status, body = client.get("/api/runs/run-a/reply?task_dir=/x")
    assert status == 404 and body["error"] == "not_found"


# ================================================== human_answer.json 打通（断链修复）----
def test_reply_writes_human_answer_json(tmp_path):
    """断链修复：reply 同时写 总日志/human_answer.json（框架 H2 口袋格式，--resume 读取）。"""
    from fwapi.dsh import reply as rp

    task_dir = make_snapshot(tmp_path, _snapshot("run-a"))
    res = rp.reply(task_dir, "run-a", {"command": "continue", "instruction": "继续跑", "module_id": "m01"})
    assert res["success"] is True
    path = os.path.join(task_dir, "总日志", "human_answer.json")
    assert os.path.isfile(path)
    doc = json.load(open(path, encoding="utf-8"))
    ans = doc["answers"]["m01"]
    assert ans["module"] == "m01"
    assert ans["code"] == "B"          # continue → 框架 B（重跑）
    assert ans["text"] == "继续跑"
    assert ans["answered_at"]


def test_reply_custom_english_maps_to_code_d(tmp_path):
    """面板英文 custom → 接受且映射框架 D（自定义）。"""
    from fwapi.dsh import reply as rp

    task_dir = make_snapshot(tmp_path, _snapshot("run-a"))
    res = rp.reply(task_dir, "run-a", {"command": "custom", "instruction": "我自己改", "module_id": "m01"})
    assert res["success"] is True
    doc = json.load(open(os.path.join(task_dir, "总日志", "human_answer.json"), encoding="utf-8"))
    assert doc["answers"]["m01"]["code"] == "D"


def test_reply_human_answer_merges_preserving_other_modules(tmp_path):
    """human_answer.json 幂等合并：保留其它模块既有条目，只更新目标模块。"""
    from fwapi.dsh import reply as rp

    task_dir = make_snapshot(tmp_path, _snapshot("run-a"))
    ha_path = os.path.join(task_dir, "总日志", "human_answer.json")
    os.makedirs(os.path.dirname(ha_path), exist_ok=True)
    with open(ha_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"answers": {"m99": {"module": "m99", "code": "A", "text": "旧"}}}))
    res = rp.reply(task_dir, "run-a", {"command": "revise", "module_id": "m01"})
    assert res["success"] is True
    doc = json.load(open(ha_path, encoding="utf-8"))
    assert "m99" in doc["answers"]     # 既有条目保留
    assert "m01" in doc["answers"]     # 新增目标条目
    assert doc["answers"]["m01"]["code"] == "B"


def test_reply_module_resolved_when_single_pending(tmp_path):
    """module_id 缺失但 needs_human 恰有一个 → 自动解析该模块。"""
    from fwapi.dsh import reply as rp

    task_dir = make_snapshot(tmp_path, _snapshot("run-a", needs_human=["m01"]))
    res = rp.reply(task_dir, "run-a", {"command": "continue"})
    assert res["success"] is True
    doc = json.load(open(os.path.join(task_dir, "总日志", "human_answer.json"), encoding="utf-8"))
    assert "m01" in doc["answers"]


def test_reply_ambiguous_module_requires_module_id(tmp_path):
    """多个 needs_human 模块且未提供 module_id → 确定性歧义错误。"""
    from fwapi.dsh import reply as rp

    task_dir = make_snapshot(tmp_path, _snapshot("run-a", needs_human=["m01", "m02"]))
    res = rp.reply(task_dir, "run-a", {"command": "continue"})
    assert res["success"] is False
    assert "module_id" in res["detail"]
