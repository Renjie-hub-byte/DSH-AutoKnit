"""Tests for fw_merge.loader."""

import os

from fw_merge.loader import (
    find_module_dirs,
    load_completed_modules,
    load_module_source,
    modules_with_src,
)

from helpers import build_sample_task


def test_find_module_dirs(sample_task):
    dirs = find_module_dirs(sample_task)
    names = sorted(os.path.basename(d) for d in dirs)
    assert names == ["mod_a", "mod_b", "mod_c"]


def test_find_module_dirs_missing(tmp_path):
    assert find_module_dirs(str(tmp_path / "nope")) == []


def test_load_module_source_parses_contract(sample_task):
    a = load_module_source(os.path.join(sample_task, "modules", "mod_a"))
    assert a.id == "mod_a"
    assert a.interface_name == "orderService"
    assert a.exports == ["dsh.orders.fetch"]
    assert a.has_src() is True


def test_load_module_source_deps_from_contract(sample_task):
    b = load_module_source(os.path.join(sample_task, "modules", "mod_b"))
    assert b.id == "mod_b"
    assert "mod_a" in b.deps


def test_modules_with_src_filters_empty(tmp_path):
    root = str(tmp_path / "t")
    os.makedirs(os.path.join(root, "modules", "empty_mod"))
    # empty_mod has no src/ -> filtered out
    got = modules_with_src(root)
    assert got == []


def test_load_completed_modules_counts(sample_task):
    mods = load_completed_modules(sample_task)
    assert len(mods) == 3
    assert all(m.has_src() for m in mods)
