"""补丁2（v0.5 故障处理）：auditor 格式不合法 → 自动重跑一次；block 无 blocker 不误判。

真实教训：lh run round_003 auditor 输出超时/格式坏导致整轮作废、executor 重做（烧 200 万+ token）。
本测试验证 _auditor_round 内置的"格式校验 + 单次重试"路径：
  1) 坏格式（root 无效 / confidence 越界 / verdict 未知）→ 自动重跑 → 第二次合法则采用
  2) 坏格式重试后仍坏 → 标记 audit_format_failed 进升级链
  3) 合法 block 但无 blocker → 不误判重试（保持 lh 既有语义：blocker 非硬性）
  4) 合法 pass 但 root 为空 → 不误判重试
"""
import pytest

from fw_runner.model import DriverOutcome
from fw_runner.runner import _auditor_round
from fw_runner.drivers import InlineAgentDriver, AgentContext
from fw_runner.events import EventLog
from fw_runner.model import ModuleSpec, RunConfig
from pathlib import Path


def _mkctx(mod_dir: Path, mid: str = "m01") -> AgentContext:
    spec = ModuleSpec(
        id=mid, name=mid, layer=1, objective="x", dependencies=[],
        dir=mod_dir, review_path=mod_dir / "REVIEW.md",
        contract_path=mod_dir / "contract.yaml",
        book_path=mod_dir / f"任务书-{mid}.yaml",
        delivery_path=mod_dir / "交付说明.md",
    )
    return AgentContext(module=spec, run_id="r1", role="auditor", round_no=1,
                        executor_id="E1", task_root=mod_dir.parent, mode="normal",
                        env={"PATH": ""})


def _run_mod(calls, mod_dir, cfg=None):
    """构造调用 _auditor_round；calls 是依次返回的 DriverOutcome 列表。"""
    it = iter(calls)
    calls_made = {"n": 0}

    def fn(ctx):
        calls_made["n"] += 1
        return next(it)

    driver = InlineAgentDriver(fn)
    events = EventLog(mod_dir.parent / "dispatch.jsonl", run_id="r1")
    ctx_spec = _mkctx(mod_dir)
    # 构造一个最小 TaskContext 形态：直接传 spec 到 actx 里即可，_auditor_round 用 ctx.modules[mid]
    from types import SimpleNamespace
    ctx = SimpleNamespace(modules={ctx_spec.module.id: ctx_spec.module},
                          task_root=mod_dir.parent)
    state = SimpleNamespace(run_id="r1")
    cfg = cfg or RunConfig()
    out = _auditor_round(driver, ctx, state, events, ctx_spec.module.id,
                         round_no=1, exec_id="E1", cfg=cfg)
    return out, calls_made, events


@pytest.fixture()
def mod_dir(tmp_path):
    d = tmp_path / "m01"
    d.mkdir(parents=True)
    (d / "交付说明.md").write_text("# 交付说明\n## 进度快照\n- 已完成: A\n- 剩余: B\n", encoding="utf-8")
    return d


@pytest.fixture()
def good_block():
    return DriverOutcome(status="ok", verdict="block", root="self",
                         confidence=0.5, reason="外部验收自测失败",
                         blocker="缺 src/REVIEW 已做")


@pytest.fixture()
def good_pass():
    return DriverOutcome(status="ok", verdict="pass", evidence_level="L2", root="",
                         confidence=0.9, reason="外部验收自测通过")


def test_bad_root_then_good_retries_once(mod_dir, good_pass):
    """坏 root → 自动重跑 → 采用第二次合法结果。"""
    bad = DriverOutcome(status="ok", verdict="block", root="weird_root",
                        confidence=0.5, reason="x")
    out, calls, events = _run_mod([bad, good_pass], mod_dir)
    assert calls["n"] == 2
    assert out.verdict == "pass"
    assert any(e["event"] == "auditor.format_invalid" for e in events.read_all())


def test_bad_confidence_retries(mod_dir, good_block):
    bad = DriverOutcome(status="ok", verdict="block", root="self",
                        confidence=2.5, reason="x")
    out, calls, _ = _run_mod([bad, good_block], mod_dir)
    assert calls["n"] == 2
    assert out.blocker == "缺 src/REVIEW 已做"


def test_retry_still_bad_marks_format_failed(mod_dir):
    bad = DriverOutcome(status="ok", verdict="block", root="nope",
                        confidence=0.5, reason="x")
    out, calls, events = _run_mod([bad, bad], mod_dir)
    assert calls["n"] == 2
    assert out.status == "error"
    assert out.detail.get("audit_format_failed")
    assert any(e["event"] == "auditor.format_failed" for e in events.read_all())


def test_block_without_blocker_not_retried(mod_dir):
    """合法 block 但无 blocker → 不误判（保持 lh 既有语义）。"""
    block_no_blk = DriverOutcome(status="ok", verdict="block", root="self",
                                 confidence=0.5, reason="self 根因")
    out, calls, _ = _run_mod([block_no_blk], mod_dir)
    assert calls["n"] == 1
    assert out.verdict == "block"


def test_pass_with_empty_root_not_retried(mod_dir):
    out, calls, _ = _run_mod([DriverOutcome(status="ok", verdict="pass", evidence_level="L2", root="",
                                            confidence=0.9, reason="ok")], mod_dir)
    assert calls["n"] == 1
    assert out.verdict == "pass"