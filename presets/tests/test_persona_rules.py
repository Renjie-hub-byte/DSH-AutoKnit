"""需求3 验收2：persona 含三权分立铁律 + 各角色协议铁律（可独立复现）。

检查对象 = 磁盘真实 preset 文件（fw-planner / fw-executor / fw-auditor 的 persona 文本
+ preset.yml description），全部只读断言。
"""
from __future__ import annotations

from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fw_presets import persona as persona_mod  # noqa: E402

PRESETS = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("name", ["fw-planner", "fw-executor", "fw-auditor"])
def test_three_presets_exist_with_persona(name: str) -> None:
    """三个 preset 目录齐全，agent.cordis.yml 可解析且含 persona 文本。"""
    text = persona_mod.get_persona_text(PRESETS / name)
    assert len(text) > 500, f"{name} persona 文本缺失或过短"


# ---------- 三权分立（公共铁律，三份 persona 都应承载） ----------

@pytest.mark.parametrize("name", ["fw-planner", "fw-executor", "fw-auditor"])
def test_three_power_separation_in_all_personas(name: str) -> None:
    """三权分立铁律出现在全部三份 persona。"""
    text = persona_mod.get_persona_text(PRESETS / name)
    assert "三权分立" in text, f"{name} persona 缺「三权分立」"


def test_iron_rule_executor_never_self_defines_acceptance() -> None:
    """最高铁律：executor 永不自定验收标准（验收2 核心）。"""
    for name in ("fw-planner", "fw-executor", "fw-auditor"):
        text = persona_mod.get_persona_text(PRESETS / name)
        assert "永不自定验收标准" in text or "不碰验收清单" in text or "不参与验收标准制定" in text, \
            f"{name} persona 未承载「executor 永不自定验收标准」铁律"


# ---------- fw-planner：prd-split 协议 + 只拆不写 ----------

def test_planner_protocol_binding() -> None:
    text = persona_mod.get_persona_text(PRESETS / "fw-planner")
    assert "prd-split" in text, "planner 未挂 prd-split 协议"
    assert "只拆不写" in text, "planner 缺「只拆不写」"
    assert "四条铁律" in text, "planner 缺 prd-split 四条铁律"
    for kw in ("变更隔离", "路径前缀+方法", "树深", "骨架先行"):
        assert kw in text, f"planner 缺 prd-split 铁律关键词: {kw}"
    assert "验收冲突" in text and "回人" in text, "planner 缺验收冲突回人定优先级"
    assert "预测基线" in text, "planner 缺预测基线"
    assert "fw-protocol" in text, "planner 缺 fw-protocol schema 引用"


def test_planner_only_splits_not_writes() -> None:
    """只拆不写：只产出规划产物，不写实现。"""
    text = persona_mod.get_persona_text(PRESETS / "fw-planner")
    assert "不写任何实现代码" in text or "绝不写任何实现代码" in text
    assert "不执行" in text and "不验收" in text


def test_planner_round_estimate_iron_rule() -> None:
    """轮数预判铁律：每个模块必填 round_estimate，超限当场切开（防 executor 撞轮数上限）。"""
    text = persona_mod.get_persona_text(PRESETS / "fw-planner")
    assert "轮数预判铁律" in text, "planner 缺轮数预判铁律"
    for kw in ("round_estimate", "executor_max_rounds 默认 5", "横向并行 A1/A2",
               "纵向串行 A1→A2", "max_rounds_override"):
        assert kw in text, f"planner 轮数预判铁律缺关键词: {kw}"


def test_planner_selfcheck_round_estimate() -> None:
    """自检包含轮数预判核对 + fw-protocol 校验兜底。"""
    text = persona_mod.get_persona_text(PRESETS / "fw-planner")
    assert "轮数预判" in text and "round_estimate" in text
    assert "拆完跑 fw-protocol 校验" in text or "fw-protocol 校验" in text


# ---------- fw-executor：执行纪律（开工先读 REVIEW.md → 列 todo → 干活 → 自测外部验收） ----------

def test_executor_discipline_chain() -> None:
    """执行纪律四步完整：开工先读 REVIEW.md → 列 todo → 干活 → 自测外部验收。"""
    text = persona_mod.get_persona_text(PRESETS / "fw-executor")
    assert "开工先读 REVIEW.md" in text or "REVIEW.md" in text, "executor 缺 REVIEW.md 步骤"
    assert "列 todo" in text, "executor 缺列 todo"
    assert "干活" in text or "动手" in text
    assert "自测" in text and "外部验收" in text, "executor 缺自测外部验收"
    assert "接受的唯一外部标准" in text or "自测的唯一外部标准" in text or "对照外部验收清单自测" in text


def test_executor_cwd_and_sandbox() -> None:
    """cwd=模块文件夹 + sandbox workspace-write（物理隔离）。"""
    text = persona_mod.get_persona_text(PRESETS / "fw-executor")
    assert "模块文件夹" in text or "cwd" in text
    assert "workspace-write" in text


def test_executor_review_write_discipline() -> None:
    """REVIEW.md 写入规矩：只写内容小节，机器键由 runner 写回。"""
    text = persona_mod.get_persona_text(PRESETS / "fw-executor")
    for kw in ("内容小节", "机器状态键", "runner"):
        assert kw in text, f"executor 缺 REVIEW 写入规矩关键词: {kw}"


# ---------- fw-auditor：验收协议（过程审计三步 + 结果对照 + 根因分类 + confidence） ----------

def test_auditor_protocol_binding() -> None:
    text = persona_mod.get_persona_text(PRESETS / "fw-auditor")
    assert "验收协议" in text or "过程审计三步" in text
    assert "过程审计三步" in text, "auditor 缺过程审计三步"
    assert "结果对照" in text, "auditor 缺结果对照"
    for kw in ("重放事件流", "对照验收清单", "测试真伪"):
        assert kw in text, f"auditor 缺三步之一关键词: {kw}"


def test_auditor_root_cause_and_confidence() -> None:
    """根因分类 self|upstream|contract + confidence 0-1。"""
    text = persona_mod.get_persona_text(PRESETS / "fw-auditor")
    for root in ("self", "upstream", "contract"):
        assert root in text, f"auditor 缺根因分类 {root}"
    assert "confidence" in text and "0-1" in text, "auditor 缺 confidence 0-1"


def test_auditor_readonly_and_four_segment() -> None:
    """sandbox read-only + 四段输出（判定/blocker/root/confidence）机器可解析。"""
    text = persona_mod.get_persona_text(PRESETS / "fw-auditor")
    assert "read-only" in text or "只读" in text, "auditor 缺只读沙箱"
    assert "AUDIT_RESULT" in text, "auditor 缺四段行格式"
    for seg in ("verdict", "blocker", "root", "confidence"):
        assert seg in text, f"auditor 四段缺: {seg}"


def test_auditor_only_judges_not_writes() -> None:
    """三权分立：auditor 只判不写（不改实现）。"""
    text = persona_mod.get_persona_text(PRESETS / "fw-auditor")
    assert "只判不写" in text or ("不改任何文件" in text and "不跑有副作用的命令" in text)


# ---------- preset.yml 也要承载关键语义（GUI 列表描述可读可见） ----------

@pytest.mark.parametrize("name,keywords", [
    ("fw-planner", ["只拆不写", "prd-split"]),
    ("fw-executor", ["永不自定验收标准", "REVIEW.md"]),
    ("fw-auditor", ["四段", "self|upstream|contract", "read-only"]),
])
def test_preset_yml_description_carries_semantics(name: str, keywords: list[str]) -> None:
    import yaml
    p = PRESETS / name / "preset.yml"
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    blob = str(doc.get("name", "")) + "\n" + str(doc.get("description", ""))
    for kw in keywords:
        assert kw in blob, f"{name}/preset.yml description 缺关键词: {kw}"
