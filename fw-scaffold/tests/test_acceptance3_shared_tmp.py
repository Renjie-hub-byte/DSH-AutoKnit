"""需求 2 验收 3：shared/ 与 tmp/ 正确区分（只读共享 vs 豁免区）。"""
from __future__ import annotations

MODULES = ["m01-数据采集", "m02-数据清洗", "m03-报表输出"]


def test_shared_is_top_level_readonly_zone(scaffolded):
    root, _ = scaffolded
    shared = root / "shared"
    assert shared.is_dir()
    assert (shared / "README.md").exists()                        # 只读规则说明
    assert (shared / ".readonly").exists()                        # 机器可识别的只读标记


def test_shared_readme_documents_readonly_rule(scaffolded):
    root, _ = scaffolded
    text = (root / "shared/README.md").read_text(encoding="utf-8")
    assert "只读" in text
    assert "不属于 auditor 豁免区" in text or "豁免" in text
    assert ".readonly" in text


def test_tmp_and_logs_are_exempt_zones(scaffolded):
    root, _ = scaffolded
    for m in MODULES:
        for sub in ("logs", "tmp"):
            marker = root / "modules" / m / sub / ".auditor-ignore"
            assert marker.exists(), f"{m}/{sub} 缺豁免区标记"
            text = marker.read_text(encoding="utf-8")
            assert "auditor" in text and "忽略" in text


def test_shared_is_not_exempt(scaffolded):
    """shared/ 不可含豁免区标记（与 logs/tmp 相反，它是被审计的只读区）。"""
    root, _ = scaffolded
    assert not (root / "shared" / ".auditor-ignore").exists()


def test_no_auditor_ignore_leaks_into_src_test(scaffolded):
    root, _ = scaffolded
    for m in MODULES:
        assert not (root / "modules" / m / "src" / ".auditor-ignore").exists()
        assert not (root / "modules" / m / "test" / ".auditor-ignore").exists()
        assert (root / "modules" / m / "src" / ".gitkeep").exists()
        assert (root / "modules" / m / "test" / ".gitkeep").exists()


def test_idempotent_rerun_without_changes(scaffolded, valid_task, tmp_path):
    """未改动的再次生成 → idempotent，不报版本冲突。"""
    out = tmp_path / "out"
    from fw_scaffold import generate
    r2 = generate(valid_task, output_dir=out)
    assert r2.guard_status == "idempotent"
