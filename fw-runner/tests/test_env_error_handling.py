"""补丁4（客观环境错误处理）：限流/断网/5xx → 识别为 upstream → 退避重试（不换人）。

真实教训：用户实测 dsh 事件流里 RATE_LIMIT(429) 重试耗尽 → turn 死 → 子代理全断。
lh 的做法是"整轮重开"（重烧上下文）；本补丁让框架在驱动层识别环境类错误，
走「同一 executor 退避重试，预算内不换人、不误判 executor」的路线。

验证三条：
  1) classify_env_error 正确识别 限流/断网/5xx → "upstream"；普通错误 → None
  2) 驱动层把 stderr 含限流信号的失败标记 root=upstream
  3) route_stuck(root=upstream) → RETRY_BACKOFF；退避预算耗尽 → HUMAN（不 SWITCH）
"""
import pytest

from fw_runner.drivers import classify_env_error
from fw_runner.upgrade import RETRY_BACKOFF, HUMAN, SWITCH, route_stuck
from fw_runner.model import RunConfig


class _State:
    """最小 RunState 替身（只暴露 route_stuck 依赖的字段）。"""
    def __init__(self):
        self.modules = {}
        self._per = {}

    def ensure(self, mid):
        import types
        st = self._per.get(mid)
        if st is None:
            st = types.SimpleNamespace(
                block_count=0, block_total=0, executor_switches=0,
                last_verdict="", root="", reason="", env_backoffs=0,
            )
            self._per[mid] = st
        return st


# ---------- 1. classify_env_error ----------

def test_classify_rate_limit():
    assert classify_env_error("HTTP 429 status code (no body) RATE_LIMIT", 1) == "upstream"
    assert classify_env_error("rate limit exceeded, retry_after=60", 1) == "upstream"
    assert classify_env_error("429 too many requests", 1) == "upstream"


def test_classify_transport():
    assert classify_env_error("connection error: ECONNREFUSED", 1) == "upstream"
    assert classify_env_error("socket hang up / transport error", 1) == "upstream"
    assert classify_env_error("Network error: failed to fetch", 1) == "upstream"


def test_classify_server():
    assert classify_env_error("500 Internal Server Error", 1) == "upstream"
    assert classify_env_error("503 service unavailable, timeout", 1) == "upstream"


def test_classify_normal_error_none():
    assert classify_env_error("ModuleNotFoundError: No module named fw_x", 1) == ""
    assert classify_env_error("pytest failed: 3 assertions", 1) == ""
    assert classify_env_error("", 1) == ""


# ---------- 2. 驱动层映射（stderr 含限流 → root=upstream）----------

def test_driver_nonzero_with_rate_limit_maps_upstream(tmp_path, monkeypatch):
    """构造一个 ScriptedAgentDriver 子进程：stderr 打 429 → 返回 root=upstream。"""
    from fw_runner.drivers import ScriptedAgentDriver
    from fw_runner.drivers import AgentContext
    from fw_runner.model import ModuleSpec

    mod_dir = tmp_path / "m01"
    mod_dir.mkdir()
    spec = ModuleSpec(
        id="m01", name="m01", layer=1, objective="x", dependencies=[],
        dir=mod_dir, review_path=mod_dir / "REVIEW.md",
        contract_path=mod_dir / "contract.yaml",
        book_path=mod_dir / "任务书-m01.yaml",
        delivery_path=mod_dir / "交付说明.md",
    )
    # 用 shell 脚本模拟 agent 打印 429 到 stderr 并退出 1
    cmd = "echo '429 status code, rate limit' >&2; exit 1"
    drv = ScriptedAgentDriver(cmd, role="executor", timeout=30)
    ctx = AgentContext(module=spec, run_id="r1", role="executor", round_no=1,
                       executor_id="E1", task_root=tmp_path, mode="normal",
                       env={"PATH": "/usr/bin:/bin:/usr/local/bin"})
    out = drv.run_round(ctx)
    assert out.status == "error"
    assert out.root == "upstream"          # 关键：环境错误被识别，不甩锅 executor
    assert "429" in out.detail.get("stderr", "")


def test_driver_normal_error_stays_self(tmp_path):
    """普通错误（非环境）→ root=self 保持原语义。"""
    from fw_runner.drivers import ScriptedAgentDriver, AgentContext
    from fw_runner.model import ModuleSpec

    mod_dir = tmp_path / "m01"
    mod_dir.mkdir()
    spec = ModuleSpec(
        id="m01", name="m01", layer=1, objective="x", dependencies=[],
        dir=mod_dir, review_path=mod_dir / "REVIEW.md",
        contract_path=mod_dir / "contract.yaml",
        book_path=mod_dir / "任务书-m01.yaml",
        delivery_path=mod_dir / "交付说明.md",
    )
    cmd = "echo 'ModuleNotFoundError: fw_x' >&2; exit 1"
    drv = ScriptedAgentDriver(cmd, role="executor", timeout=30)
    ctx = AgentContext(module=spec, run_id="r1", role="executor", round_no=1,
                       executor_id="E1", task_root=tmp_path, mode="normal",
                       env={"PATH": "/usr/bin:/bin:/usr/local/bin"})
    out = drv.run_round(ctx)
    assert out.status == "error"
    assert out.root == "self"


# ---------- 3. route_stuck(root=upstream) → 退避重试，不换人 ----------

def test_upstream_routes_backoff_not_switch():
    state = _State()
    cfg = RunConfig()
    action = route_stuck(state, "m01", cfg, "429 rate limit", root="upstream")
    assert action == RETRY_BACKOFF                    # 不退避换人！
    assert state.ensure("m01").env_backoffs == 1


def test_upstream_exhausts_backoff_goes_human_not_switch():
    state = _State()
    cfg = RunConfig()
    for _ in range(3):                                # 3 次退避预算
        action = route_stuck(state, "m01", cfg, "429", root="upstream")
    assert action == HUMAN                            # 预算耗尽回人
    assert state.ensure("m01").env_backoffs == 3
    assert state.ensure("m01").executor_switches == 0  # 从未换 executor


def test_normal_stuck_still_switches():
    """非环境卡死 → 保持原语义（SWITCH 换人），不误入退避分支。"""
    state = _State()
    cfg = RunConfig()
    action = route_stuck(state, "m01", cfg, "executor_max_rounds", root="self")
    assert action == SWITCH or action == HUMAN
    assert state.ensure("m01").env_backoffs == 0