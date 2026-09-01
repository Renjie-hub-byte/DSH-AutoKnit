"""Tests for the fw-merge CLI (dispatch + subcommands + outputs)."""

import json
import os

from fw_merge.cli import main


def _files(root):
    return {
        f"{os.path.relpath(os.path.join(dp, fn), root)}"
        for dp, _dn, fns in os.walk(root)
        for fn in fns
    }


def test_run_subcommand_writes_outputs(sample_task, tmp_path):
    out = str(tmp_path / "out")
    rc = main(["run", sample_task, "--output-dir", out])
    assert rc == 0
    for name in ("skeleton.json", "conflicts.json"):
        p = os.path.join(out, name)
        assert os.path.isfile(p), f"missing {name}"
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
        assert isinstance(data, list)
    # per-module interface files exist
    ifaces = _files(os.path.join(out, "interfaces"))
    assert "mod_a/interface.json" in ifaces
    assert "mod_b/interface.json" in ifaces
    assert "mod_c/interface.json" in ifaces


def test_bare_task_root_behaves_as_run(sample_task, tmp_path):
    out = str(tmp_path / "out")
    rc = main([sample_task, "--output-dir", out])
    assert rc == 0
    assert os.path.isfile(os.path.join(out, "conflicts.json"))


def test_skeleton_subcommand_prints_json(sample_task, capsys):
    rc = main(["skeleton", sample_task])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert isinstance(payload, list)


def test_conflicts_subcommand_prints_json(sample_task, capsys):
    rc = main(["conflicts", sample_task])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert isinstance(payload, list)
    assert {i["kind"] for i in payload} >= {"same_name", "naming_conflict"}


def test_interfaces_subcommand_prints_paths(sample_task, tmp_path, capsys):
    out = str(tmp_path / "out")
    rc = main(["interfaces", sample_task, "--output-dir", out])
    printed = capsys.readouterr().out.strip().splitlines()
    assert rc == 0
    assert len(printed) == 3
    for p in printed:
        assert os.path.isfile(p)


def test_api_subcommand(sample_task, capsys):
    rc = main(["api", "dsh.merge.skeleton", sample_task])
    out = capsys.readouterr().out
    assert rc == 0
    assert isinstance(json.loads(out), list)


def test_no_args_prints_help():
    rc = main([])
    assert rc == 0


def test_missing_task_is_tolerated_as_empty_run(capsys):
    # a nonexistent task root simply yields empty skeleton/conflicts
    rc = main(["conflicts", "/nonexistent/xyz"])
    out = capsys.readouterr().out
    assert rc == 0
    assert json.loads(out) == []


def test_explicit_missing_db_is_tolerated(sample_task, capsys):
    # an explicit --db that cannot be found degrades to contract-deps graph
    rc = main(["conflicts", sample_task, "--db", "/nonexistent/codegraph.db"])
    out = capsys.readouterr().out
    assert rc == 0
    assert isinstance(json.loads(out), list)


def test_run_writes_compile_notes_and_wiring(final_task, tmp_path):
    out = str(tmp_path / "out")
    rc = main(["run", final_task, "--output-dir", out])
    assert rc == 0
    # new artifacts written by run
    with open(os.path.join(out, "compile_notes.json"), encoding="utf-8") as fh:
        notes = json.load(fh)
    assert isinstance(notes, list) and notes
    assert all(n["compiles"] is False for n in notes)
    with open(os.path.join(out, "wiring.json"), encoding="utf-8") as fh:
        wiring = json.load(fh)
    assert isinstance(wiring, list)
    # per-module wiring files exist
    assert "mod_a/wiring.json" in _files(os.path.join(out, "wiring"))


def test_wiring_subcommand_prints_json(wiring_task, tmp_path, capsys):
    out = str(tmp_path / "out")
    rc = main(["wiring", wiring_task, "--output-dir", out])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    by_id = {w["module"]: w for w in payload}
    assert "mod_b" in by_id
    assert any("interfaces/mod_a/interface.json" in r for r in by_id["mod_b"]["requires"])


def test_notes_subcommand_prints_json(final_task, capsys):
    rc = main(["notes", final_task])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert {n["reason"] for n in payload} == {
        "same_name",
        "naming_conflict",
        "signature_mismatch",
        "semantic_merge",
    }
