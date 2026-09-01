"""REVIEW.md 读写助手单元测试（交接/判定载体）。"""
from __future__ import annotations

from fw_runner.review import append_done, append_todo, fingerprint, read_review, set_values

from helpers import build_task, module


def test_review_key_values_and_sections(single_root):
    mdir = next((single_root / "modules").iterdir())
    rp = mdir / "REVIEW.md"
    set_values(rp, status="working", executor_round="1", executor_id="E1", root="self")
    append_todo(rp, "todo-A")
    assert append_done(rp, "完成 A") is True
    assert append_done(rp, "完成 A") is False   # 不重复
    doc = read_review(rp)
    assert doc.kv["status"] == "working"
    assert doc.kv["executor_round"] == "1"
    assert doc.kv["executor_id"] == "E1"
    assert doc.kv["root"] == "self"
    assert any("todo-A" in ln for ln in doc.list_todo())
    assert any("完成 A" in ln for ln in doc.list_done())


def test_fingerprint_changes_with_work(single_root):
    mdir = next((single_root / "modules").iterdir())
    from fw_runner.context import load_task_context
    ctx = load_task_context(single_root)
    spec = ctx.modules["m01"]
    fp0 = fingerprint(spec)
    # executor 干活 → 指纹变化（实质产出）
    append_done(spec.review_path, "做了一点事")
    (spec.dir / "src" / "x.txt").write_text("x\n", encoding="utf-8")
    fp1 = fingerprint(spec)
    assert fp0 != fp1
    # 只动 tmp/（豁免区）→ 指纹不变
    (spec.dir / "tmp" / "noise.txt").write_text("n\n", encoding="utf-8")
    assert fingerprint(spec) == fp1
