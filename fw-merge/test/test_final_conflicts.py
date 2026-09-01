"""Final-block conflict tests: all four kinds + compile-readiness notes.

Covers the two newly added kinds (``signature_mismatch`` and ``semantic_merge``)
and asserts the merged output covers every contract conflict kind.
"""

from fw_merge.conflicts import (
    aggregate_conflicts,
    detect_semantic_merges,
    detect_signature_mismatches,
)
from fw_merge.loader import modules_with_src
from fw_merge.skeleton import build_skeleton


def test_all_four_kinds_aggregated(final_task):
    from fw_merge.depgraph import ModuleGraph

    modules = modules_with_src(final_task)
    plan = build_skeleton(modules, ModuleGraph())
    conflicts = aggregate_conflicts(modules, plan)

    kinds = {c.kind for c in conflicts}
    assert kinds == {
        "same_name",
        "naming_conflict",
        "signature_mismatch",
        "semantic_merge",
    }

    # every item matches the contract item shape
    for c in conflicts:
        assert set(c.to_dict().keys()) == {
            "kind",
            "module_refs",
            "description",
            "needs_human",
        }
        assert c.needs_human is True


def test_signature_mismatch_detected(final_task):
    from fw_merge.depgraph import ModuleGraph

    modules = modules_with_src(final_task)
    mismatches = detect_signature_mismatches(modules)
    assert len(mismatches) == 1
    c = mismatches[0]
    assert c.kind == "signature_mismatch"
    assert set(c.module_refs) == {"mod_a", "mod_b"}
    assert "parse" in c.description
    assert c.needs_human is True


def test_semantic_merge_detected(final_task):
    from fw_merge.depgraph import ModuleGraph

    modules = modules_with_src(final_task)
    plan = build_skeleton(modules, ModuleGraph())
    merges = detect_semantic_merges(modules, plan)
    assert len(merges) == 1
    c = merges[0]
    assert c.kind == "semantic_merge"
    assert set(c.module_refs) == {"mod_a", "mod_b"}
    assert "util.py" in c.description
    assert c.needs_human is True


def test_api_conflicts_covers_all_kinds(final_task):
    from fw_merge import api

    payload = api.get("dsh.merge.conflicts", final_task)
    kinds = {i["kind"] for i in payload}
    assert kinds == {
        "same_name",
        "naming_conflict",
        "signature_mismatch",
        "semantic_merge",
    }
    for item in payload:
        assert set(item.keys()) == {
            "kind",
            "module_refs",
            "description",
            "needs_human",
        }


def test_compile_notes_explain_non_compilable_points(final_task):
    from fw_merge.compile import build_compile_notes
    from fw_merge.depgraph import ModuleGraph

    modules = modules_with_src(final_task)
    plan = build_skeleton(modules, ModuleGraph())
    conflicts = aggregate_conflicts(modules, plan)
    notes = build_compile_notes(modules, plan, conflicts)

    assert notes
    for n in notes:
        assert n.compiles is False
        assert n.reason in {
            "same_name",
            "naming_conflict",
            "signature_mismatch",
            "semantic_merge",
        }
        assert n.explanation

    reasons = {n.reason for n in notes}
    # same_name (dup/thing.py held aside) + the other three kinds
    assert reasons == {
        "same_name",
        "naming_conflict",
        "signature_mismatch",
        "semantic_merge",
    }
    # the held-aside module for the same-name path is mod_c
    same = next(n for n in notes if n.reason == "same_name")
    assert same.module == "mod_c"
    assert same.target_path == "dup/thing.py"
