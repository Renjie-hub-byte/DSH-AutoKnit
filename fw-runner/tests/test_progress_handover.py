"""可靠性补丁（功能B）：换 executor / 回人时从进度继续（不从头）。

修复的真实运行教训：换人时新 executor 不知道旧 executor 干到哪一步。本测试证明：
1. auditor block → 换新 executor 时：REVIEW.md 出现「进度指针」小节（前任 已完成/剩余，
   机器可解析）；
2. 交接 bundle（logs/handover-*）内含 前任 交付说明.md 全文 + 「从『剩余』继续，
   不要重做已完成部分」提示词；
3. 回人（finalize_human，root=upstream 等）时 REVIEW 同样落进度指针，真人接手信息完备；
4. upsert 进度指针 幂等（换两次人只保留最新一份，键值行不受影响）。
"""
from __future__ import annotations

from fw_runner.context import load_task_context
from fw_runner.drivers import InlineAgentDriver
from fw_runner.model import DriverOutcome
from fw_runner.progress import write_progress
from fw_runner.review import append_done, read_review, upsert_section_file
from fw_runner.runner import run


def _module_dir(single_root):
    ctx = load_task_context(single_root)
    return next(iter(ctx.modules.values())).dir


def _pointer_block(doc) -> str:
    for title, lines in doc.sections.items():
        if title == "进度指针" or title.startswith("进度指针"):
            return "\n".join(lines)
    return ""


def test_switch_carries_previous_progress(single_root):
    """block 2 次 → 换 E2：REVIEW 进度指针 + 交接 bundle 含前任 交付说明.md 全文与续作指令。"""

    def executor(ctx):
        write_progress(ctx.module.delivery_path,
                       done="已完成改造；接口已对齐 contract.yaml",
                       remaining="模块乙：未接入集成测试",
                       executor_id=ctx.executor_id, round_no=ctx.round_no)
        append_done(ctx.module.review_path, f"exec {ctx.round_no} ({ctx.executor_id})")
        return DriverOutcome(status="ok", substance=True, tokens=0)

    def auditor(ctx):
        return DriverOutcome(status="ok", verdict="block", root="self", confidence=0.4,
                             reason="验收不过（演示常 block）", blocker="演示 blocker")

    result = run(single_root, executor_driver=InlineAgentDriver(executor),
                 auditor_driver=InlineAgentDriver(auditor))

    assert result.status == "needs_human"
    mdir = _module_dir(single_root)

    # 1) REVIEW.md 进度指针：前任/已完成/剩余 机器可解析
    doc = read_review(mdir / "REVIEW.md")
    pointer = _pointer_block(doc)
    assert pointer, "REVIEW.md 缺「进度指针」小节（换 executor 必须交代前任进度）"
    assert "前任 executor:" in pointer
    assert "已完成" in pointer and "接口已对齐 contract.yaml" in pointer
    assert "剩余" in pointer and "未接入集成测试" in pointer

    # 2) 交接 bundle：含 前任交付说明.md 全文 + 新任须知（从剩余继续、不重做已完成）
    bundles = list((mdir / "logs").glob("handover-*"))
    assert len(bundles) == 1, f"预期 1 个交接 bundle，实际 {len(bundles)}"
    bt = bundles[0].read_text(encoding="utf-8")
    assert "===== 交付说明.md =====" in bt          # 前任 交付说明.md 全文随 bundle 交接
    assert "未接入集成测试" in bt                     # 全文内容在（非占位）
    assert "前任 executor: E1" in bt                 # 交接发生在 E1→E2 的时刻
    assert "从「剩余」继续" in bt
    assert "不要重做" in bt


def test_human_return_has_progress_pointer(single_root):
    """回人（root=upstream，第一轮即回）时 REVIEW 落进度指针：REVIEW 已做 兜底为已完成。"""

    def executor(ctx):
        append_done(ctx.module.review_path, "已实现 m01 全部功能")
        return DriverOutcome(status="ok", substance=True, tokens=0)

    def auditor(ctx):
        return DriverOutcome(status="ok", verdict="block", root="upstream",
                             confidence=0.6, reason="上游未交付，无法验收", blocker="上游 blocker")

    result = run(single_root, executor_driver=InlineAgentDriver(executor),
                 auditor_driver=InlineAgentDriver(auditor))

    assert result.status == "needs_human"
    mdir = _module_dir(single_root)
    doc = read_review(mdir / "REVIEW.md")
    pointer = _pointer_block(doc)
    assert pointer, "回人必须留下进度指针（真人接手信息完备）"
    assert "已实现 m01 全部功能" in pointer          # REVIEW 已做 兜底
    assert "剩余" in pointer
    assert "完成" in pointer
    # 无换人 → 不应生成交接 bundle
    assert list((mdir / "logs").glob("handover-*")) == []


def test_upsert_pointer_section_preserves_review(single_root):
    """进度指针小节 upsert：新增/替换幂等，机器键值行不受影响。"""
    mdir = _module_dir(single_root)
    upsert_section_file(mdir / "REVIEW.md", "进度指针", ["- 已完成: A", "- 剩余: B"])
    doc = read_review(mdir / "REVIEW.md")
    assert "已完成: A" in _pointer_block(doc)
    assert doc.kv.get("status") == "pending"        # 顶层键值保留
    assert doc.kv.get("executor_round") == "0"
    # 再次 upsert → 整块替换，不追加重复
    upsert_section_file(mdir / "REVIEW.md", "进度指针", ["- 已完成: C", "- 剩余: D"])
    doc = read_review(mdir / "REVIEW.md")
    block = _pointer_block(doc)
    assert block.count("已完成: C") == 1
    assert "已完成: A" not in block
    assert doc.kv.get("status") == "pending"