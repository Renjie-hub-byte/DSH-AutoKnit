"""fs 原子写 + expected 版本防护（manifest 指纹守卫）。"""
from __future__ import annotations

import json

import pytest

from fw_scaffold import ExpectedVersionMismatch, generate
from fw_scaffold.io_utils import atomic_write_text, guard_existing_dir, sha256_bytes


def test_atomic_write_creates_and_replaces(tmp_path):
    p = tmp_path / "nested" / "f.txt"
    atomic_write_text(p, "v1")
    assert p.read_text(encoding="utf-8") == "v1"
    atomic_write_text(p, "v2")
    assert p.read_text(encoding="utf-8") == "v2"
    # 无残留临时文件
    leftovers = [f.name for f in p.parent.iterdir() if f.name.startswith(".tmp-")]
    assert leftovers == []


def test_atomic_write_overwrites_existing(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("old", encoding="utf-8")
    atomic_write_text(p, "new content")
    assert p.read_text(encoding="utf-8") == "new content"


def test_guard_fresh_and_idempotent(valid_task, tmp_path):
    out = tmp_path / "out"
    r1 = generate(valid_task, output_dir=out)
    assert r1.guard_status == "fresh"
    r2 = generate(valid_task, output_dir=out)
    assert r2.guard_status == "idempotent"          # 未改动 → 幂等重跑


def test_guard_rejects_modified_generated_file(valid_task, tmp_path):
    out = tmp_path / "out"
    r1 = generate(valid_task, output_dir=out)
    target = r1.root / "skeleton.md"
    target.write_text(target.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")
    with pytest.raises(ExpectedVersionMismatch) as ei:
        generate(valid_task, output_dir=out)
    assert "skeleton.md" in str(ei.value)


def test_guard_rejects_different_task_in_same_dir(valid_task, tmp_path):
    """同名任务书但内容不同（预算改）→ 将写入的 task.yaml 指纹变化 → 拒绝（expected 版本防护）。"""
    import yaml
    out = tmp_path / "out"
    r1 = generate(valid_task, output_dir=out)
    doc = yaml.safe_load(valid_task.read_text(encoding="utf-8"))
    doc["budget"]["max_tokens"] = 999999                      # 同名同日期 → 同目录，但 effective 内容变
    variant = tmp_path / "variant.yaml"
    variant.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    with pytest.raises(ExpectedVersionMismatch) as ei:
        generate(variant, output_dir=out)
    assert "指纹不一致" in str(ei.value)


def test_guard_rejects_nonempty_dir_without_manifest(valid_task, tmp_path):
    out = tmp_path / "out"
    stray = out / "任务-测试订单管道_2026-08-21"
    stray.mkdir(parents=True)
    (stray / "user.txt").write_text("mine", encoding="utf-8")
    with pytest.raises(ExpectedVersionMismatch) as ei:
        generate(valid_task, output_dir=out)
    assert "无 .scaffold-manifest.json" in str(ei.value)


def test_force_overrides_mismatch(valid_task, tmp_path):
    out = tmp_path / "out"
    r1 = generate(valid_task, output_dir=out)
    target = r1.root / "skeleton.md"
    target.write_text(target.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")
    r2 = generate(valid_task, output_dir=out, force=True)
    assert r2.guard_status == "forced"
    # 覆盖后 manifest 刷新，幂等成立
    r3 = generate(valid_task, output_dir=out)
    assert r3.guard_status == "idempotent"


def test_empty_dir_treated_as_fresh(valid_task, tmp_path):
    out = tmp_path / "out"
    (out / "任务-测试订单管道_2026-08-21").mkdir(parents=True)
    r = generate(valid_task, output_dir=out)
    assert r.guard_status == "fresh"


def test_dry_run_writes_nothing(valid_task, tmp_path):
    out = tmp_path / "out"
    r = generate(valid_task, output_dir=out, dry_run=True)
    assert r.files
    assert not (out / "任务-测试订单管道_2026-08-21").exists()


def test_manifest_records_all_generated_files(valid_task, tmp_path):
    out = tmp_path / "out"
    r = generate(valid_task, output_dir=out)
    manifest = json.loads((r.root / ".scaffold-manifest.json").read_text(encoding="utf-8"))
    assert set(manifest["files"].keys()) == set(r.files)
    assert manifest["schema_version"] == 2
    # 每个记录的 hash 与磁盘一致
    for rel in r.files:
        from fw_scaffold.io_utils import sha256_file
        assert sha256_file(r.root / rel) == manifest["files"][rel]
    assert (r.root / ".scaffold-version").exists()
