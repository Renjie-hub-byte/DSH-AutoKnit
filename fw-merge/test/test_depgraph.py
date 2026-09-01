"""Tests for fw_merge.depgraph."""

import sqlite3

import pytest

from fw_merge.depgraph import DepGraphReader, ModuleGraph, default_db_candidates

from helpers import build_codegraph_db, build_sample_task  # noqa: E402


def reader():
    return DepGraphReader()


def test_read_sample_db_modules(sample_task):
    db = f"{sample_task}/framework-v1/.codegraph/codegraph.db"
    graph = reader().read(db)
    assert set(graph.modules()) == {"mod_a", "mod_b", "mod_c"}


def test_read_sample_db_dependencies(sample_task):
    db = f"{sample_task}/framework-v1/.codegraph/codegraph.db"
    graph = reader().read(db)
    assert graph.direct_deps("mod_b") == ["mod_a"]
    assert graph.direct_deps("mod_a") == []


def test_topological_order_is_dependency_first(sample_task):
    db = f"{sample_task}/framework-v1/.codegraph/codegraph.db"
    graph = reader().read(db)
    order = graph.topological_order()
    # mod_a (dependency of mod_b) must come before mod_b
    assert order.index("mod_a") < order.index("mod_b")


def test_branches(sample_task):
    db = f"{sample_task}/framework-v1/.codegraph/codegraph.db"
    graph = reader().read(db)
    branches = graph.branches()
    # mod_a must appear in the same branch as mod_b (mod_b depends on mod_a)
    assert any("mod_a" in b and "mod_b" in b for b in branches)
    # within a branch, dependency (mod_a) precedes dependent (mod_b)
    branch = next(b for b in branches if "mod_a" in b and "mod_b" in b)
    assert branch.index("mod_a") < branch.index("mod_b")


def test_missing_db_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        reader().read(str(tmp_path / "nope.db"))


def test_empty_db_gives_empty_graph(tmp_path):
    db = str(tmp_path / "empty.db")
    build_codegraph_db(db, nodes=[], edges=[])
    graph = reader().read(db)
    assert graph.modules() == []
    assert graph.topological_order() == []


def test_db_without_edges_still_reads_nodes(tmp_path):
    db = str(tmp_path / "nodes_only.db")
    nodes = [
        ("n1", "a", "modules/mx/src/a.py", "mx"),
        ("n2", "b", "modules/my/src/b.py", "my"),
    ]
    build_codegraph_db(db, nodes=nodes, edges=[])
    graph = reader().read(db)
    assert set(graph.modules()) == {"mx", "my"}
    assert graph.direct_deps("mx") == []
    assert graph.direct_deps("my") == []


def test_derives_module_from_path_when_no_module_column(tmp_path):
    db = str(tmp_path / "nocol.db")
    build_codegraph_db(db, nodes=[("n1", "x", "modules/m02/src/x.py", "")], edges=[])
    graph = reader().read(db)
    assert graph.modules() == ["m02"]


def test_default_db_candidates(tmp_path):
    root = str(tmp_path / "t")
    build_codegraph_db(
        f"{root}/framework-v1/.codegraph/codegraph.db", nodes=[], edges=[]
    )
    cands = default_db_candidates(root)
    assert any(c.endswith("framework-v1/.codegraph/codegraph.db") for c in cands)


def test_module_graph_add_dep_dedupes_self():
    g = ModuleGraph()
    g.add_dep("a", "a")
    assert g.direct_deps("a") == []
