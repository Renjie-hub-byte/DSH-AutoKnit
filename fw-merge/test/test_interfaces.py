"""Tests for fw_merge.interfaces."""

import json
import os

from fw_merge.depgraph import DepGraphReader
from fw_merge.interfaces import (
    build_interface_specs,
    list_interface_files,
    write_interface_files,
)
from fw_merge.loader import modules_with_src


def _load(sample_task):
    modules = modules_with_src(sample_task)
    graph = DepGraphReader().read(f"{sample_task}/framework-v1/.codegraph/codegraph.db")
    return modules, graph


def test_build_interface_specs(sample_task):
    modules, graph = _load(sample_task)
    specs = build_interface_specs(modules, graph)
    by = {s.module: s for s in specs}
    assert set(by) == {"mod_a", "mod_b", "mod_c"}
    assert by["mod_a"].name == "orderService"
    assert "mod_a" in by["mod_b"].deps  # dependency from graph


def test_write_interface_files_creates_files(sample_task, tmp_path):
    modules, graph = _load(sample_task)
    out = str(tmp_path / "out")
    paths = write_interface_files(modules, graph, out)
    assert len(paths) == 3  # one per module
    for p in paths:
        assert os.path.isfile(p)
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
        assert {"name", "module", "exports", "deps"} <= set(data.keys())


def test_list_interface_files(sample_task, tmp_path):
    modules, graph = _load(sample_task)
    out = str(tmp_path / "out")
    write_interface_files(modules, graph, out)
    listed = list_interface_files(out)
    assert len(listed) == 3
    assert all(p.endswith("interface.json") for p in listed)


def test_list_interface_files_when_absent(tmp_path):
    assert list_interface_files(str(tmp_path / "empty")) == []
