"""Tests for fw_merge.skeleton (directory placement + same_name conflicts)."""

from fw_merge.depgraph import DepGraphReader
from fw_merge.loader import modules_with_src
from fw_merge.model import SkeletonEntry
from fw_merge.skeleton import build_skeleton


def _setup(task_root):
    modules = modules_with_src(task_root)
    graph = DepGraphReader().read(f"{task_root}/framework-v1/.codegraph/codegraph.db")
    plan = build_skeleton(modules, graph)
    return modules, graph, plan


def test_skeleton_has_dir_and_file_entries(sample_task):
    _, _, plan = _setup(sample_task)
    kinds = {e.kind for e in plan.entries()}
    assert "dir" in kinds
    assert "file" in kinds
    # every entry is a SkeletonEntry with contract fields
    for e in plan.entries():
        assert isinstance(e, SkeletonEntry)
        assert e.target_path and e.source_module


def test_same_name_conflict_detected(sample_task):
    _, _, plan = _setup(sample_task)
    same = [c for c in plan.conflicts if c.kind == "same_name"]
    assert any("foo/util.py" in c.description for c in same)
    c = next(c for c in same if "foo/util.py" in c.description)
    assert set(c.module_refs) == {"mod_a", "mod_b"}
    assert c.needs_human is True


def test_dependency_first_module_owns_duplicate_path(sample_task):
    _, graph, plan = _setup(sample_task)
    order = graph.topological_order()
    assert order.index("mod_a") < order.index("mod_b")
    owner = plan.target_path_owner.get("foo/util.py")
    assert owner == "mod_a"  # dependency-first provider keeps the placement
    # mod_b's duplicate is held aside pending human decision
    assert "mod_b" in plan.held_aside.get("foo/util.py", [])


def test_benign_duplicate_init_not_a_conflict(tmp_path):
    from helpers import build_sample_task

    root = build_sample_task(str(tmp_path / "t"))
    _, _, plan = _setup(root)
    # no conflict on __init__.py even though mod_a and mod_c both have one
    assert not any(
        c.kind == "same_name" and "__init__.py" in c.description
        for c in plan.conflicts
    )
