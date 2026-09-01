"""Tests for fw_merge.conflicts (naming_conflict + aggregation)."""

from fw_merge.conflicts import aggregate_conflicts
from fw_merge.depgraph import DepGraphReader, ModuleGraph
from fw_merge.loader import modules_with_src
from fw_merge.skeleton import build_skeleton


def test_naming_conflict_detected(sample_task):
    modules = modules_with_src(sample_task)
    graph = DepGraphReader().read(f"{sample_task}/framework-v1/.codegraph/codegraph.db")
    plan = build_skeleton(modules, graph)
    conflicts = aggregate_conflicts(modules, plan)

    kinds = {c.kind for c in conflicts}
    assert "same_name" in kinds
    assert "naming_conflict" in kinds

    nc = next(c for c in conflicts if c.kind == "naming_conflict")
    # mod_a("orderService") vs mod_b("orderservice") spell the same interface
    assert set(nc.module_refs) == {"mod_a", "mod_b"}
    assert nc.needs_human is True


def test_no_conflicts_when_clean(tmp_path):
    from helpers import _write

    root = str(tmp_path / "clean")
    _write(f"{root}/modules/mx/src/a.py", "x=1\n")
    _write(f"{root}/modules/mx/interface.json", '{"name": "catalog"}\n')
    _write(f"{root}/modules/my/src/b.py", "y=2\n")
    _write(f"{root}/modules/my/interface.json", '{"name": "orders"}\n')

    modules = modules_with_src(root)
    plan = build_skeleton(modules, ModuleGraph())
    conflicts = aggregate_conflicts(modules, plan)
    assert conflicts == []
