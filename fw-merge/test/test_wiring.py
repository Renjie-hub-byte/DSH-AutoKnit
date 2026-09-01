"""Tests for fw_merge.wiring (require/import pins to dependency interfaces)."""

from fw_merge import api
from fw_merge.engine import MergeEngine
from fw_merge.interfaces import write_interface_files
from fw_merge.loader import modules_with_src
from fw_merge.model import WiringSpec
from fw_merge.wiring import build_wiring_specs, extract_imports, write_wiring_files


def test_extract_imports_from_module(wiring_task):
    from fw_merge.loader import load_completed_modules

    mod_b = next(m for m in load_completed_modules(wiring_task) if m.id == "mod_b")
    imports = extract_imports(mod_b.src_dir)
    assert ("mod_a", "helper") in imports


def test_build_wiring_pins_point_to_dependency_interface(wiring_task, tmp_path):
    from fw_merge.depgraph import ModuleGraph

    modules = modules_with_src(wiring_task)
    graph = ModuleGraph()
    for m in modules:
        graph.deps.setdefault(m.id, set()).update(m.deps)

    out = str(tmp_path / "out")
    write_interface_files(modules, graph, out)
    specs = build_wiring_specs(modules, graph, out)

    by_id = {s.module: s for s in specs}
    assert set(by_id) == {"mod_a", "mod_b"}

    mb = by_id["mod_b"]
    assert isinstance(mb, WiringSpec)
    # requires = interface file of direct dependency mod_a
    assert mb.requires == [f"{out}/interfaces/mod_a/interface.json"]
    # imports resolves the `helper` symbol to mod_a's interface file
    assert any(
        i["symbol"] == "helper"
        and i["from"] == "mod_a"
        and i["interface_file"] == f"{out}/interfaces/mod_a/interface.json"
        for i in mb.imports
    )


def test_wiring_files_written_by_engine(wiring_task, tmp_path):
    result = MergeEngine().run(wiring_task, output_root=str(tmp_path / "out"))
    assert any(path.endswith("/wiring/mod_b/wiring.json") for path in result.wiring_files)
    import json
    with open(f"{tmp_path}/out/wiring/mod_b/wiring.json", encoding="utf-8") as fh:
        data = json.load(fh)
    assert data["module"] == "mod_b"
    assert data["requires"] == [f"{tmp_path}/out/interfaces/mod_a/interface.json"]
    assert any(i["symbol"] == "helper" for i in data["imports"])


def test_run_writes_wiring_artifact(wiring_task, tmp_path):
    api.get("dsh.merge.conflicts", wiring_task)  # ensure pipeline runs
    result = MergeEngine().run(wiring_task, output_root=str(tmp_path / "out"))
    assert any(path.endswith("wiring.json") for path in result.wiring_files)
