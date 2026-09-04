"""fwapi.serve 收官轮新增端点测试：GET /api/runs/{id}/usage（dsh.task.usage）。

2026-09-01 数据流重写后适配：usage 改为会话索引直查（session_index），不再
子进程调 fw-token.py；对拍基准改为直接调 fw-token CLI（口径统一后两者一致）。

覆盖意图（与重写前一致）：
- 空降级：空 run_id / 目录缺失 / run 未命中 / 无模块可拆 → 确定性结构；
- per-module 拆分与直接调 fw-token 对拍一致；run 级 = planner + Σ模块 + other；
- 模块 id 严格段匹配（m03 不串 m03a）；run 时间窗过滤窗口外会话；
- planner 归根级会话（窗口内），模块会话/未来会话不串；
- 进行中 run 回退注册表 started_at 作窗口起点。

口径（2026-09-01 Owner拍板）：会话 inputTokens 即非缓存输入；billable =
input + output（缓存读单独上报不计费）；total_input = input + cache_read。
"""
import json
import os
import subprocess
import sys
from urllib.parse import urlencode

import pytest

from conftest import make_registry, make_snapshot  # noqa: F401

from fwapi.dsh import usage as usage_source
from fwapi.dsh.session_index import SessionIndex, encode_task_dir

_RUN_ID = "run-usage-test"
_M01_START = "2026-08-26T22:25:42+08:00"
_M03A_START = "2026-08-26T22:59:27+08:00"
# registry 起点早于首模块 5 分钟（planner 在 [reg_start, m01_start) 执行）。
_RUN_REG_START = "2026-08-26T22:20:42+08:00"


def _entry(ms, inp, out, cache):
    """构造一条可解析的会话 jsonl 记录（usage 嵌套两层，兼容新版格式）。"""
    return {
        "time": ms,
        "data": {"chunk": {"usage": {
            "inputTokens": inp, "outputTokens": out, "cacheReadTokens": cache,
        }}},
    }


def _write_session(sess_root, dirname, sess_id, entries):
    """把 entries 写成 <sess_root>/<dirname>/session-<id>/session.jsonl.zstd。"""
    d = os.path.join(sess_root, dirname, f"session-{sess_id}")
    os.makedirs(d, exist_ok=True)
    payload = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries)
    target = os.path.join(d, "session.jsonl.zstd")
    proc = subprocess.run(["zstd", "-q", "-f", "-o", target], input=payload.encode("utf-8"))
    assert proc.returncode == 0, "zstd 压缩失败（对拍测试依赖 zstd CLI）"
    return target


def _snapshot(modules, per_module, run_id=_RUN_ID):
    """构造快照字典（run_id/modules/per_module）。"""
    return {
        "run_id": run_id,
        "task": "usage-test-task",
        "modules": modules,
        "per_module": per_module,
        "needs_human": [],
    }


def _direct_fw_token(fw_token, dsh_home, module, since_ms):
    """直接调 fw-token.py --json（对拍基准），返回其合计 JSON。"""
    env = dict(os.environ)
    env["DSH_HOME"] = dsh_home
    proc = subprocess.run(
        [sys.executable, fw_token, "--json", "--since", str(int(since_ms)), module],
        capture_output=True, env=env, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    return json.loads(proc.stdout.decode("utf-8"))


def _find_fw_token():
    """定位真实 fw-token.py：env → PATH → 沿父目录找 framework-v1/fw-tools。"""
    import shutil
    env = os.environ.get("FW_TOKEN_PY", "").strip()
    if env and os.path.isfile(env):
        return env
    on_path = shutil.which("fw-token.py")
    if on_path:
        return on_path
    cur = os.path.dirname(os.path.abspath(__file__))
    for _ in range(14):
        cand = os.path.join(cur, "framework-v1", "fw-tools", "fw-token.py")
        if os.path.isfile(cand):
            return cand
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return None


def _build_index(monkeypatch, tmp_path, task_dir=None):
    """构造隔离 DSH_HOME + 同步索引，返回 (dsh_home, fw_token, index)。"""
    fw_token = _find_fw_token()
    assert fw_token, "找不到 fw-token.py（对拍测试需真实 fw-token 复用）"

    dsh_home = str(tmp_path / "dsh-home")
    sess_root = os.path.join(dsh_home, "sessions")
    os.makedirs(sess_root, exist_ok=True)

    monkeypatch.setenv("DSH_HOME", dsh_home)
    monkeypatch.setenv("FW_TOKEN_PY", fw_token)
    index = SessionIndex()
    index.refresh()
    return dsh_home, fw_token, index


def _task_enc(task_dir):
    """任务目录的会话目录名编码前缀（cwd 在任务目录内的会话目录名以它开头）。"""
    return "--" + encode_task_dir(task_dir)


# ---------------------------------------------------------------- 空降级 ----
def test_run_usage_empty_run_id():
    """空 run_id → 确定性空降级：run/planner/other 全 0、per_module 空。"""
    body = usage_source.run_usage("", "")
    assert body["run"]["input"] == 0 and body["run"]["billable"] == 0
    assert body["planner"]["input"] == 0 and body["other"]["input"] == 0
    assert body["per_module"] == {}
    assert body["no_split"] == "无拆分数据"


def test_run_usage_dir_missing():
    """目录缺失 → 确定性空降级，不抛异常。"""
    body = usage_source.run_usage("/no/such/dir", _RUN_ID)
    assert body["run"]["input"] == 0 and body["run"]["billable"] == 0
    assert body["per_module"] == {}
    assert body["no_split"] == "无拆分数据"


def test_run_usage_run_not_found(tmp_path, monkeypatch):
    """run 未命中（快照 run_id 不匹配）→ 空降级。"""
    task_dir = make_snapshot(tmp_path, _snapshot({"m01": "done"}, {"m01": {"started_at": _M01_START}}))
    body = usage_source.run_usage(task_dir, "other-run")
    assert body["run"]["input"] == 0 and body["per_module"] == {}
    assert body["no_split"] == "无拆分数据"


def test_run_usage_no_split_data(tmp_path, monkeypatch):
    """快照命中但无模块可拆 → 标「无拆分数据」。"""
    task_dir = make_snapshot(tmp_path, _snapshot({}, {}))
    body = usage_source.run_usage(task_dir, _RUN_ID)
    assert body["per_module"] == {}
    assert body["no_split"] == "无拆分数据"


def test_run_usage_no_matching_sessions(tmp_path, monkeypatch):
    """索引无匹配会话 → 有模块结构但数值全 0（确定性降级，不抛异常）。"""
    task_dir = make_snapshot(tmp_path, _snapshot(
        {"m01": "done"},
        {"m01": {"started_at": _M01_START}},
    ))
    _, _, index = _build_index(monkeypatch, tmp_path)
    body = usage_source.run_usage(task_dir, _RUN_ID, index=index)
    assert body["per_module"]["m01"]["input"] == 0
    assert body["run"]["input"] == 0 and body["run"]["billable"] == 0
    assert body["no_split"] == ""


# ---------------------------------------------------------------- 对拍 ----
def test_run_usage_matches_fw_token_direct(tmp_path, monkeypatch):
    """per-module 拆分与直接调 fw-token 逐模块对拍一致；run 级 = planner + Σ模块 + other。"""
    task_dir = make_snapshot(tmp_path, _snapshot(
        {"m01": "done", "m03a": "done"},
        {
            "m01": {"started_at": _M01_START, "ended_at": "2026-08-26T22:40:08+08:00"},
            "m03a": {"started_at": _M03A_START, "ended_at": "2026-08-26T23:11:42+08:00"},
        },
    ))
    dsh_home, fw_token, index = _build_index(monkeypatch, tmp_path)
    sess_root = os.path.join(dsh_home, "sessions")
    task_enc = _task_enc(task_dir)

    run_start_ms = usage_source._to_ms(_M01_START)
    # m01：两个当前会话 + 一个窗口外旧会话（验证时间窗过滤）。
    _write_session(sess_root, f"{task_enc}-modules-m01--", "a", [
        _entry(run_start_ms, 100, 50, 20),
        _entry(run_start_ms + 60_000, 30, 10, 5),
    ])
    _write_session(sess_root, f"{task_enc}-modules-m01--", "old", [
        _entry(run_start_ms - 3_600_000, 9999, 9999, 9999),  # 时间窗外 → 应被过滤
    ])
    _write_session(sess_root, f"{task_enc}-modules-m01--", "b", [
        _entry(run_start_ms + 1_000, 10, 5, 0),
    ])
    _write_session(sess_root, f"{task_enc}-modules-m03a--", "c", [
        _entry(usage_source._to_ms(_M03A_START), 200, 100, 40),
    ])
    index.refresh()

    body = usage_source.run_usage(task_dir, _RUN_ID, index=index)
    assert body["no_split"] == ""

    # 对拍：每个模块 our 结果 == 直接调 fw-token --json（口径统一后 billable 亦一致）。
    for mid, expect in (
        ("m01", {"input": 140, "output": 65, "cache_read": 25, "calls": 3, "billable": 205}),
        ("m03a", {"input": 200, "output": 100, "cache_read": 40, "calls": 1, "billable": 300}),
    ):
        direct = _direct_fw_token(fw_token, dsh_home, mid, run_start_ms)
        assert body["per_module"][mid]["input"] == direct["input_tokens"]
        assert body["per_module"][mid]["output"] == direct["output_tokens"]
        assert body["per_module"][mid]["cache_read"] == direct["cache_read_tokens"]
        assert body["per_module"][mid]["calls"] == direct["calls"]
        assert body["per_module"][mid]["billable"] == direct["billable_tokens"]
        assert body["per_module"][mid]["billable"] == expect["billable"], f"模块 {mid} 对拍不一致"

    # run 级 = planner(0) + Σ模块 + other(0)（本测无根级会话）。
    run = body["run"]
    assert run["input"] == 340 and run["output"] == 165
    assert run["cache_read"] == 65 and run["calls"] == 4
    assert run["billable"] == 505  # 口径：input + output（非缓存计费）


def test_run_usage_module_id_strict(tmp_path, monkeypatch):
    """模块 id 严格匹配：m03 不串扰 m03a（段匹配语义保持）。"""
    _setup_env_full(monkeypatch, tmp_path)
    dsh_home = os.environ["DSH_HOME"]
    fw_token = os.environ["FW_TOKEN_PY"]
    direct = _direct_fw_token(fw_token, dsh_home, "m03", usage_source._to_ms(_M01_START))
    assert direct["sessions"] == 0


def _setup_env_full(monkeypatch, tmp_path):
    """旧对拍测试的会话构造（CLI 对拍用，任务目录无关）。"""
    dsh_home, fw_token, _ = _build_index(monkeypatch, tmp_path)
    sess_root = os.path.join(dsh_home, "sessions")
    run_start_ms = usage_source._to_ms(_M01_START)
    _write_session(sess_root, "-task-modules-m01", "a", [
        _entry(run_start_ms, 100, 50, 20),
        _entry(run_start_ms + 60_000, 30, 10, 5),
    ])
    _write_session(sess_root, "-task-modules-m03a", "c", [
        _entry(usage_source._to_ms(_M03A_START), 200, 100, 40),
    ])
    return dsh_home, fw_token


def test_run_usage_time_window_excludes_old(tmp_path, monkeypatch):
    """run 时间窗过滤窗口外会话：只统计 run 开始后的 usage。"""
    dsh_home, fw_token = _setup_env_full(monkeypatch, tmp_path)
    sess_root = os.path.join(dsh_home, "sessions")
    run_start_ms = usage_source._to_ms(_M01_START)
    _write_session(sess_root, "-task-modules-m01", "old", [
        _entry(run_start_ms - 3_600_000, 9999, 9999, 9999),
    ])
    direct = _direct_fw_token(fw_token, dsh_home, "m01", run_start_ms)
    # 窗口外会话（9999 tokens）被过滤，故 m01 仅保留两段当前会话。
    assert direct["input_tokens"] == 130
    assert direct["output_tokens"] == 60


# ======================================================== 任务目录归属 ----
def test_run_usage_prefix_boundary(tmp_path, monkeypatch):
    """cwd 归属用前缀 + 结尾边界：任务A 的会话不串进前缀相同的 任务A-b。"""
    task_a = make_snapshot(tmp_path, _snapshot({"m01": "done"}, {"m01": {"started_at": _M01_START}}))
    task_b = task_a + "-b"  # 模拟互为前缀的另一个任务目录
    os.makedirs(os.path.join(task_b, "总日志"), exist_ok=True)
    with open(os.path.join(task_b, "总日志", "快照.json"), "w", encoding="utf-8") as fh:
        json.dump(_snapshot({"m01": "done"}, {"m01": {"started_at": _M01_START}}), fh, ensure_ascii=False)

    dsh_home, _, index = _build_index(monkeypatch, tmp_path)
    sess_root = os.path.join(dsh_home, "sessions")
    t = usage_source._to_ms(_M01_START)
    # 会话 cwd 落在 task_a 内（编码名以 task_a 编码 + "-" 开头后接子目录）。
    _write_session(sess_root, f"{_task_enc(task_a)}-modules-m01--", "x", [_entry(t, 111, 22, 3)])
    index.refresh()

    body_a = usage_source.run_usage(task_a, _RUN_ID, index=index)
    assert body_a["per_module"]["m01"]["input"] == 111
    # task_b 目录无任何会话 → 全 0（旧子串匹配会把 task_a 会话串进来）。
    body_b = usage_source.run_usage(task_b, _RUN_ID, index=index)
    assert body_b["per_module"]["m01"]["input"] == 0


# ---------------------------------------------------------------- HTTP ----
def test_run_usage_http_empty(live_server):
    """GET /api/runs/{id}/usage 目录缺失 → HTTP 200 确定性空降级结构。"""
    client, _ = live_server
    status, body = client.get(f"/api/runs/{_RUN_ID}/usage?task_dir=/no/such/dir")
    assert status == 200
    assert body["run"]["input"] == 0 and body["per_module"] == {}
    assert body["no_split"] == "无拆分数据"


def test_run_usage_http_e2e(live_server, tmp_path, monkeypatch):
    """GET /api/runs/{id}/usage 端到端：HTTP 返回与数据源层一致。"""
    from fwapi.dsh import session_index as si

    task_dir = make_snapshot(tmp_path, _snapshot(
        {"m01": "done"},
        {"m01": {"started_at": _M01_START}},
    ))
    dsh_home, _, _ = _build_index(monkeypatch, tmp_path)
    sess_root = os.path.join(dsh_home, "sessions")
    t = usage_source._to_ms(_M01_START)
    _write_session(sess_root, f"{_task_enc(task_dir)}-modules-m01--", "a", [
        _entry(t, 100, 50, 20),
        _entry(t + 60_000, 30, 10, 5),
        _entry(t + 1_000, 10, 5, 0),
    ])

    # 进程内单例与测试环境同步（HTTP handler 走 get_index()）。
    si.reset_index()
    idx = si.get_index()
    idx.refresh()

    client, _ = live_server
    status, body = client.get(f"/api/runs/{_RUN_ID}/usage?task_dir={task_dir}")
    assert status == 200
    assert body["no_split"] == ""
    assert body["per_module"]["m01"]["input"] == 140
    assert body["per_module"]["m01"]["billable"] == 205
    assert body["run"]["input"] == 140 and body["run"]["billable"] == 205
    si.reset_index()


def test_run_usage_post_not_allowed(live_server):
    """GET-only 端点发 POST → 404（路由不匹配，错误信封）。"""
    client, _ = live_server
    status, body = client.post("/api/runs/x/usage", {})
    assert status == 404
    assert body["error"] == "not_found"


# ======================================================== planner / other 桶 ----
def test_planner_usage_aggregates_root_session(monkeypatch, tmp_path):
    """planner 桶：根级会话落在 [run 起点, 首模块起点) → 聚合进 planner。"""
    task_dir = make_snapshot(tmp_path, _snapshot(
        {"m01": "done"}, {"m01": {"started_at": _M01_START}}))
    dsh_home, _, index = _build_index(monkeypatch, tmp_path)
    sess_root = os.path.join(dsh_home, "sessions")
    reg_ms = usage_source._to_ms(_RUN_REG_START)
    _write_session(sess_root, _task_enc(task_dir) + "--", "plan", [
        _entry(reg_ms + 120_000, 500, 200, 1000),
        _entry(reg_ms + 180_000, 100, 50, 0),
    ])
    index.refresh()

    reg_path = make_registry(tmp_path, [{
        "run_id": _RUN_ID, "task_dir": task_dir, "task": "t", "status": "active",
        "started_at": _RUN_REG_START, "updated_at": _RUN_REG_START,
    }])
    monkeypatch.setenv("AUTOKNIT_RUNS_REGISTRY", reg_path)

    body = usage_source.run_usage(task_dir, _RUN_ID, index=index)
    assert body["planner"]["input"] == 600
    assert body["planner"]["output"] == 250
    assert body["planner"]["cache_read"] == 1000
    assert body["planner"]["calls"] == 2
    # 口径：billable = input + output（非缓存），缓存单独一列。
    assert body["planner"]["billable"] == 850
    # 规划耗时 = 窗口内会话时间窗（两条调用相隔 60s）。
    assert body["planner"]["duration_ms"] == 60_000


def test_planner_usage_ignores_module_sessions(monkeypatch, tmp_path):
    """模块会话（cwd 含 modules 段）不当作 planner。"""
    task_dir = make_snapshot(tmp_path, _snapshot(
        {"m01": "done"}, {"m01": {"started_at": _M01_START}}))
    dsh_home, _, index = _build_index(monkeypatch, tmp_path)
    sess_root = os.path.join(dsh_home, "sessions")
    t = usage_source._to_ms(_M01_START)
    _write_session(sess_root, f"{_task_enc(task_dir)}-modules-m01--", "a", [
        _entry(t, 100, 50, 20),
    ])
    index.refresh()
    body = usage_source.run_usage(task_dir, _RUN_ID, index=index)
    assert body["planner"]["input"] == 0 and body["planner"]["calls"] == 0
    assert body["per_module"]["m01"]["input"] == 100


def test_planner_usage_skips_future_sessions(monkeypatch, tmp_path):
    """结束晚于首模块起点的根级会话归 other（integration/总检），不算 planner。

    会话是原子单位：tmax > first_module 的会话整体走 other 分支（其中首模块
    之前的行也不算 planner）；planner 取「结束 ≤ first_module 的最近一个会话」。
    """
    task_dir = make_snapshot(tmp_path, _snapshot(
        {"m01": "done"}, {"m01": {"started_at": _M01_START}}))
    dsh_home, _, index = _build_index(monkeypatch, tmp_path)
    sess_root = os.path.join(dsh_home, "sessions")
    reg_ms = usage_source._to_ms(_RUN_REG_START)
    _write_session(sess_root, _task_enc(task_dir) + "--", "plan", [
        _entry(reg_ms + 60_000, 300, 100, 0),              # planner 会话（结束早于首模块）
    ])
    _write_session(sess_root, _task_enc(task_dir) + "--", "later", [
        _entry(usage_source._to_ms(_M01_START) + 3_600_000, 999, 999, 999),
    ])
    index.refresh()

    reg_path = make_registry(tmp_path, [{
        "run_id": _RUN_ID, "task_dir": task_dir, "task": "t", "status": "active",
        "started_at": _RUN_REG_START, "updated_at": _RUN_REG_START,
    }])
    monkeypatch.setenv("AUTOKNIT_RUNS_REGISTRY", reg_path)

    body = usage_source.run_usage(task_dir, _RUN_ID, index=index)
    assert body["planner"]["input"] == 300
    assert body["other"]["input"] == 999 and body["other"]["calls"] == 1


def test_run_usage_includes_planner_field(monkeypatch, tmp_path):
    """run 级 = planner + Σ模块 + other：planner 会话计入总消耗且分项可对账。"""
    task_dir = make_snapshot(tmp_path, _snapshot(
        {"m01": "done"}, {"m01": {"started_at": _M01_START}}))
    dsh_home, _, index = _build_index(monkeypatch, tmp_path)
    sess_root = os.path.join(dsh_home, "sessions")
    reg_ms = usage_source._to_ms(_RUN_REG_START)
    t = usage_source._to_ms(_M01_START)
    _write_session(sess_root, _task_enc(task_dir) + "--", "plan", [
        _entry(reg_ms + 60_000, 300, 120, 500),
    ])
    _write_session(sess_root, f"{_task_enc(task_dir)}-modules-m01--", "a", [
        _entry(t, 100, 50, 20),
        _entry(t + 1_000, 10, 5, 0),
        _entry(t + 60_000, 30, 10, 5),
    ])
    index.refresh()

    reg_path = make_registry(tmp_path, [{
        "run_id": _RUN_ID, "task_dir": task_dir, "task": "t", "status": "active",
        "started_at": _RUN_REG_START, "updated_at": _RUN_REG_START,
    }])
    monkeypatch.setenv("AUTOKNIT_RUNS_REGISTRY", reg_path)

    body = usage_source.run_usage(task_dir, _RUN_ID, index=index)
    assert body["planner"]["input"] == 300 and body["planner"]["output"] == 120
    assert body["planner"]["billable"] == 420
    assert body["per_module"]["m01"]["input"] == 140
    # run 级 = planner + m01：140+300=440 输入，65+120=185 输出。
    assert body["run"]["input"] == 440
    assert body["run"]["output"] == 185
    assert body["run"]["billable"] == 440 + 185
    assert body["no_split"] == ""


def test_run_usage_in_progress_falls_back_to_registry_start(tmp_path, monkeypatch):
    """进行中的 run（per_module 空）→ 时间窗回退用注册表 started_at，过滤历史会话。

    回归：进行中 run 的 per_module 尚未写入 started_at；修复后窗口起点用注册表
    started_at，只统计 run 开始后的会话。
    """
    dsh_home, fw_token, index = _build_index(monkeypatch, tmp_path)
    task_dir = make_snapshot(tmp_path, _snapshot({"m01": "pending"}, {}))
    sess_root = os.path.join(dsh_home, "sessions")
    task_enc = _task_enc(task_dir)
    t = usage_source._to_ms(_M01_START)
    _write_session(sess_root, f"{task_enc}-modules-m01--", "a", [
        _entry(t, 100, 50, 20),
        _entry(t + 60_000, 30, 10, 5),
    ])
    _write_session(sess_root, f"{task_enc}-modules-m01--", "old", [
        _entry(t - 3_600_000, 9999, 9999, 9999),
    ])
    index.refresh()

    reg_path = make_registry(tmp_path, [{
        "run_id": _RUN_ID,
        "task_dir": task_dir,
        "task": "usage-test-task",
        "status": "active",
        "started_at": _M01_START,
        "updated_at": _M01_START,
    }])
    monkeypatch.setenv("AUTOKNIT_RUNS_REGISTRY", reg_path)

    body = usage_source.run_usage(task_dir, _RUN_ID, index=index)

    # m01 应只统计 run 开始后的会话（100+30=130 / 50+10=60），窗口外的 9999 被过滤。
    assert body["per_module"]["m01"]["input"] == 130
    assert body["per_module"]["m01"]["output"] == 60
    assert body["run"]["billable"] == 190
    assert body["no_split"] == ""


def test_planner_session_in_parent_dir(monkeypatch, tmp_path):
    """planner 真实布局（实测）：会话 cwd = 任务目录的父目录 → 命中 planner。

    回归：planner 以 fw-run 启动 cwd（任务目录父目录）开会话，且结束时间早于
    registry 登记时刻——归属必须用「结束 ≤ 首模块起点的最近一个会话」而非
    since 行级下界，否则 planner 恒 0。
    """
    task_dir = make_snapshot(tmp_path, _snapshot(
        {"m01": "done"}, {"m01": {"started_at": _M01_START}}))
    dsh_home, _, index = _build_index(monkeypatch, tmp_path)
    sess_root = os.path.join(dsh_home, "sessions")
    parent_enc = "--" + encode_task_dir(os.path.dirname(task_dir.rstrip("/"))) + "--"
    reg_ms = usage_source._to_ms(_RUN_REG_START)
    _write_session(sess_root, parent_enc, "plan", [
        _entry(reg_ms + 30_000, 400, 150, 20),   # 结束早于 registry started_at（真实时序）
    ])
    # 干扰项：无关目录的根级会话（同一父目录下的其它工作）不算。
    _write_session(sess_root, "--Users-demo-somewhere-else--", "noise", [
        _entry(reg_ms + 40_000, 8888, 8888, 8888),
    ])
    index.refresh()

    reg_path = make_registry(tmp_path, [{
        "run_id": _RUN_ID, "task_dir": task_dir, "task": "t", "status": "active",
        "started_at": _RUN_REG_START, "updated_at": _RUN_REG_START,
    }])
    monkeypatch.setenv("AUTOKNIT_RUNS_REGISTRY", reg_path)

    body = usage_source.run_usage(task_dir, _RUN_ID, index=index)
    assert body["planner"]["input"] == 400
    assert body["planner"]["output"] == 150
    assert body["planner"]["calls"] == 1
    assert body["other"]["input"] == 0


# ======================================================== 同 task 多 run 切分 ----
def test_planner_first_module_equals_registry_start(monkeypatch, tmp_path):
    """回归（2026-09-01 实测）：first_module == registry started_at（登记与
    dispatch 同一刻）时，planner 会话（结束早于登记时刻）仍须命中——
    「最近一个 ≤ first_module」分支不能要求 first_module 严格大于 since。"""
    task_dir = make_snapshot(tmp_path, _snapshot(
        {"m01": "done"}, {"m01": {"started_at": _M01_START}}))
    dsh_home, _, index = _build_index(monkeypatch, tmp_path)
    sess_root = os.path.join(dsh_home, "sessions")
    parent_enc = "--" + encode_task_dir(os.path.dirname(task_dir.rstrip("/"))) + "--"
    # registry started_at == m01 started_at == M01_START；planner 会话结束早于它。
    t0 = usage_source._to_ms(_M01_START)
    _write_session(sess_root, parent_enc, "plan", [
        _entry(t0 - 120_000, 450, 160, 30),
        _entry(t0 - 60_000, 100, 40, 0),
    ])
    index.refresh()

    reg_path = make_registry(tmp_path, [{
        "run_id": _RUN_ID, "task_dir": task_dir, "task": "t", "status": "active",
        "started_at": _M01_START, "updated_at": _M01_START,
    }])
    monkeypatch.setenv("AUTOKNIT_RUNS_REGISTRY", reg_path)

    body = usage_source.run_usage(task_dir, _RUN_ID, index=index)
    assert body["planner"]["input"] == 550
    assert body["planner"]["output"] == 200
    assert body["planner"]["calls"] == 2
    assert body["planner"]["duration_ms"] == 60_000
    # run 级含 planner（本测未写模块会话，m01 桶为 0）。
    assert body["run"]["input"] == 550


def test_run_usage_same_task_dir_multi_run_window(tmp_path, monkeypatch):
    """同 task_dir 多 run：窗口上界（下一 run 起点）把会话精确切开，旧 run 不吞新 run。

    真实约束：快照.json 是单份的（只描述最新 run），故只有快照 run_id 命中的
    run 能拆分；窗口上界防的是「快照还是旧 run 时，新 run 的会话被旧 run 吞掉」
    的串扰双计（旧实现 --since 无上界的补丁债）。
    """
    task_dir = make_snapshot(tmp_path, _snapshot(
        {"m01": "done"}, {"m01": {"started_at": _M01_START}}, run_id="run-1"))
    dsh_home, _, index = _build_index(monkeypatch, tmp_path)
    sess_root = os.path.join(dsh_home, "sessions")
    task_enc = _task_enc(task_dir)
    t1 = usage_source._to_ms(_M01_START)                 # run1 起点也是 m01 起点
    t2 = t1 + 3_600_000                                   # run2 起点晚 1h
    t2_iso = "2026-08-26T23:25:42+08:00"
    _write_session(sess_root, f"{task_enc}-modules-m01--", "r1", [
        _entry(t1 + 60_000, 100, 50, 20),                 # run1 的会话
    ])
    _write_session(sess_root, f"{task_enc}-modules-m01--", "r2", [
        _entry(t2 + 60_000, 700, 70, 7),                  # run2 的会话（晚于 run1 窗口上界）
    ])
    index.refresh()

    reg_path = make_registry(tmp_path, [
        {"run_id": "run-1", "task_dir": task_dir, "task": "t", "status": "complete",
         "started_at": _M01_START, "updated_at": _M01_START},
        {"run_id": "run-2", "task_dir": task_dir, "task": "t", "status": "complete",
         "started_at": t2_iso, "updated_at": t2_iso},
    ])
    monkeypatch.setenv("AUTOKNIT_RUNS_REGISTRY", reg_path)

    # run-1（快照命中）：窗口 [t1, t2) → 只统计自己的会话。
    body1 = usage_source.run_usage(task_dir, "run-1", index=index)
    assert body1["per_module"]["m01"]["input"] == 100
    assert body1["per_module"]["m01"]["output"] == 50
    # run-2：快照仍是 run-1 的 → 确定性空降级（不编造，不串 run-1 的数）。
    body2 = usage_source.run_usage(task_dir, "run-2", index=index)
    assert body2["per_module"] == {}
    assert body2["no_split"] == "无拆分数据"
