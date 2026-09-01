"""Compile-readiness report for the merged skeleton.

The task's acceptance criterion is "the merged skeleton compiles, *or* for any
point that cannot compile, the conflict list gives a clear explanation".  We
deliberately take the second branch: module code is not executed or compiled,
so we derive *explicit* notes about which modules/paths cannot be expected to
compile after the merge, and why.  These notes are surfaced both as part of the
conflict descriptions and as a dedicated ``compile_notes.json`` artifact (the
"明确说明路径").

A point is marked non-compilable when a conflict makes its target file unusable:

* ``same_name``        — the module's file was held aside (a dependency-first
                         module owns the target path), so that module's copy
                         is not present at the path its importers expect.
* ``naming_conflict``  — importers cannot resolve the interface name.
* ``signature_mismatch`` — callers cannot bind a single signature.
* ``semantic_merge``   — content needs manual fusion before it can compile.
"""

from __future__ import annotations

import json
import os
from typing import List

from .loader import ModuleSource
from .model import CompileNote, Conflict
from .skeleton import MergePlan


def build_compile_notes(
    modules: List[ModuleSource], plan: MergePlan, conflicts: List[Conflict]
) -> List[CompileNote]:
    """Derive non-compilable-point notes from the plan and conflict list."""
    notes: List[CompileNote] = []

    # same_name: every held-aside module cannot compile at that target path.
    for tpath in sorted(plan.held_aside):
        for mod in sorted(plan.held_aside[tpath]):
            notes.append(
                CompileNote(
                    module=mod,
                    compiles=False,
                    reason="same_name",
                    target_path=tpath,
                    explanation=(
                        f"合并骨架中 {tpath!r} 由依赖先模块占位, 模块 {mod!r} 的"
                        f"同路径文件被 held-aside 待人工决定; 该模块此路径不可编译, "
                        f"需人工决定保留哪份或改写路径。"
                    ),
                )
            )

    # conflicts that affect whole modules (not a single path).
    for c in conflicts:
        if c.kind == "naming_conflict":
            for m in c.module_refs:
                notes.append(
                    CompileNote(
                        module=m,
                        compiles=False,
                        reason=c.kind,
                        target_path="",
                        explanation=(
                            f"接口名不唯一({c.description}); importers 无法解析, "
                            f"该模块相关接线不可编译, 需人工统一命名。"
                        ),
                    )
                )
        elif c.kind == "signature_mismatch":
            for m in c.module_refs:
                notes.append(
                    CompileNote(
                        module=m,
                        compiles=False,
                        reason=c.kind,
                        target_path="",
                        explanation=(
                            f"接口签名不一致({c.description}); 调用方无法绑定单一签名, "
                            f"该模块相关接线不可编译, 需人工统一签名或加适配层。"
                        ),
                    )
                )
        elif c.kind == "semantic_merge":
            for m in c.module_refs:
                notes.append(
                    CompileNote(
                        module=m,
                        compiles=False,
                        reason=c.kind,
                        target_path="",
                        explanation=(
                            f"需要语义融合({c.description}); 自动化合并不做语义级融合, "
                            f"人工统一前该处不可编译。"
                        ),
                    )
                )

    # de-duplicate on (module, reason, target_path), keep first explanation.
    seen, out = set(), []
    for note in notes:
        dedup_key = (note.module, note.reason, note.target_path)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        out.append(note)
    out.sort(key=lambda n: (n.module, n.reason, n.target_path))
    return out


def write_compile_notes(output_root: str, notes: List[CompileNote]) -> str:
    """Write ``compile_notes.json`` under ``output_root`` and return its path."""
    os.makedirs(output_root, exist_ok=True)
    path = os.path.join(output_root, "compile_notes.json")
    payload = [n.to_dict() for n in notes]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return path


__all__ = ["build_compile_notes", "write_compile_notes"]
