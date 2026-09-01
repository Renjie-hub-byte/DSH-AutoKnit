"""v1.0 升级链：SPLIT/UPGRADE_MODEL 常量、route_verdict 新顺序（B1/B2）、
route_partial 四分支（B3）、should_merge_back 阈值（B4）。"""
from __future__ import annotations

from fw_runner.model import RunConfig, RunState
from fw_runner.upgrade import (
    DONE,
    HUMAN,
    RETRY,
    SPLIT,
    SWITCH,
    UPGRADE_MODEL,
    route_partial,
    route_verdict,
    should_merge_back,
)


def test_split_and_upgrade_model_constants():
    # B1：新增动作常量
    assert SPLIT == "split"
    assert UPGRADE_MODEL == "upgrade"
    assert DONE == "done"
    assert RETRY == "retry"
    assert SWITCH == "switch"
    assert HUMAN == "human"


def test_route_verdict_new_chain_order():
    """B2：升级链新顺序 retry → switch → split → upgrade → human（顺序不可乱）。

    模拟 runner 的副作用（switch 清零 block_count / 换 executor、_do_split 抬高
    split_depth、_upgrade_model 抬高 model_tier），逐步走完整条链。
    """
    state = RunState()
    cfg = RunConfig()  # retry_before_switch=2, max_executor_switches=1,
                       # split_max_depth=2, enable_split=True, enable_fallback_model=True,
                       # model_tiers=["flash","pro"]
    astate = state.ensure("m01")

    seq = [route_verdict(state, "m01", cfg, root="self")]          # block1 → RETRY
    seq.append(route_verdict(state, "m01", cfg, root="self"))      # block2 → SWITCH
    astate.executor_switches += 1                                   # 模拟 switch_executor
    astate.block_count = 0                                          # 模拟 switch_executor 清零
    seq.append(route_verdict(state, "m01", cfg, root="self"))      # block3 → RETRY（新 executor）
    seq.append(route_verdict(state, "m01", cfg, root="self"))      # block4 → SPLIT
    astate.split_depth = cfg.split_max_depth                        # 模拟 _do_split 抬高深度
    seq.append(route_verdict(state, "m01", cfg, root="self"))      # block5 → UPGRADE_MODEL
    astate.model_tier = 1                                           # 模拟 _upgrade_model
    seq.append(route_verdict(state, "m01", cfg, root="self"))      # block6 → HUMAN

    assert seq == [RETRY, SWITCH, RETRY, SPLIT, UPGRADE_MODEL, HUMAN], seq


def test_route_verdict_split_skipped_when_disabled():
    """B2：enable_split=False 时 switch 用尽 → 直接 UPGRADE_MODEL（不拆）。"""
    state = RunState()
    cfg = RunConfig(enable_split=False)
    astate = state.ensure("m01")
    route_verdict(state, "m01", cfg, root="self")      # block1 RETRY
    route_verdict(state, "m01", cfg, root="self")      # block2 SWITCH
    astate.executor_switches += 1
    astate.block_count = 0
    route_verdict(state, "m01", cfg, root="self")      # block3 RETRY
    assert route_verdict(state, "m01", cfg, root="self") == UPGRADE_MODEL


def test_route_verdict_fallback_skipped_when_disabled():
    """B2：enable_fallback_model=False 时 split 到上限 → 直接 HUMAN。"""
    state = RunState()
    cfg = RunConfig(enable_fallback_model=False)
    astate = state.ensure("m01")
    astate.split_depth = cfg.split_max_depth           # split 已到上限
    astate.executor_switches = cfg.max_executor_switches
    astate.block_count = cfg.retry_before_switch
    assert route_verdict(state, "m01", cfg, root="self") == HUMAN


def test_route_verdict_upstream_contract_still_human():
    """B2：root=upstream/contract 仍直接回人，不重试不拆。"""
    for root in ("upstream", "contract"):
        state = RunState()
        assert route_verdict(state, "m01", RunConfig(), root=root) == HUMAN
        assert state.ensure("m01").block_total == 1


def test_route_partial_min_deliverables_precheck_retry():
    """v2 贪心：剩余 1 项 ≤ 阈值 → RETRY（剩得不多，做完就好，不拆）。"""
    state = RunState()
    cfg = RunConfig(split_min_deliverables=2)
    assert route_partial(state, "m01", cfg, passed_count=1, total_count=2,
                         remaining_items=["b"]) == RETRY


def test_route_partial_high_ratio_retry():
    """v2 贪心：剩余 ≤ 阈值 → RETRY（同 executor 续做，不看完成度）。"""
    state = RunState()
    assert route_partial(state, "m01", RunConfig(), passed_count=7, total_count=10,
                         remaining_items=["x", "y"]) == RETRY          # 剩 2（=阈值）
    assert route_partial(state, "m01", RunConfig(), passed_count=8, total_count=10,
                         remaining_items=["x"]) == RETRY                # 剩 1


def test_route_partial_remaining_many_split_immediate():
    """v2 贪心：剩余 > 阈值 → 立即 SPLIT（跳过无谓续做，拆给下一个）。"""
    state = RunState()
    cfg = RunConfig()
    items = ["a", "b", "c"]  # 3 项 > 阈值 2
    assert route_partial(state, "m01", cfg, passed_count=7, total_count=10,
                         remaining_items=items) == SPLIT


def test_route_partial_remaining_many_split_low_ratio():
    """v2 贪心：低完成度但剩余多 → 也立即 SPLIT（不因完成度低而多续做）。"""
    state = RunState()
    assert route_partial(state, "m01", RunConfig(), passed_count=3, total_count=10,
                         remaining_items=["a", "b", "c", "d", "e", "f", "g"]) == SPLIT


def test_route_partial_no_split_capacity_human():
    """v2 贪心：剩余多但不能拆（enable_split=False / split_depth 到上限）→ 立即 HUMAN。"""
    state = RunState()
    cfg = RunConfig(enable_split=False)
    items = ["a", "b", "c", "d", "e", "f", "g"]
    assert route_partial(state, "m01", cfg, passed_count=3, total_count=10,
                         remaining_items=items) == HUMAN
    state2 = RunState()
    state2.ensure("m01").split_depth = 2                                # split_max_depth 默认 2
    assert route_partial(state2, "m01", RunConfig(), passed_count=3, total_count=10,
                         remaining_items=items) == HUMAN


def test_route_partial_increments_partial_count():
    """B3/B4 衔接：route_partial 每次判定 partial_count +1（供 should_merge_back 使用）。"""
    state = RunState()
    cfg = RunConfig()
    route_partial(state, "m01", cfg, passed_count=3, total_count=10,
                  remaining_items=["a", "b", "c", "d", "e", "f", "g"])
    route_partial(state, "m01", cfg, passed_count=3, total_count=10,
                  remaining_items=["a", "b", "c", "d", "e", "f", "g"])
    assert state.ensure("m01").partial_count == 2


def test_should_merge_back_threshold():
    """B4：partial_count >= split_merge_after_fails → 合并回父。"""
    state = RunState()
    cfg = RunConfig(split_merge_after_fails=3)
    assert should_merge_back(state, "m01", cfg) is False               # 初始 0

    astate = state.ensure("m01")
    astate.partial_count = 2
    assert should_merge_back(state, "m01", cfg) is False               # 2 < 3 不合并

    astate.partial_count = 3
    assert should_merge_back(state, "m01", cfg) is True                # 3 >= 3 合并
    astate.partial_count = 4
    assert should_merge_back(state, "m01", cfg) is True                # 超阈值也合并


def test_should_merge_back_custom_threshold():
    """B4：阈值由 split_merge_after_fails 控制。"""
    state = RunState()
    state.ensure("m01").partial_count = 3
    assert should_merge_back(state, "m01", RunConfig(split_merge_after_fails=4)) is False
    assert should_merge_back(state, "m01", RunConfig(split_merge_after_fails=3)) is True
