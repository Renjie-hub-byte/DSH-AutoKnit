"""End-to-end merge pipeline for fw-merge.

:class:`MergeEngine` ties the pieces together:

    load completed modules -> read dependency graph (codegraph.db)
        -> build merged skeleton (tree-branch placement)
        -> aggregate conflicts (same_name + naming_conflict +
           signature_mismatch + semantic_merge)
        -> compile-readiness notes for non-compilable points
        -> write per-module target interface files + require/import wiring pins

It is deliberately dependency-free at the LLM level: everything is pure
Python + stdlib + pyyaml, and the pipeline never issues any model request.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional

from .compile import build_compile_notes, write_compile_notes
from .conflicts import aggregate_conflicts
from .depgraph import (
    DepGraphReader,
    ModuleGraph,
    default_db_candidates,
)
from .interfaces import list_interface_files, write_interface_files
from .loader import ModuleSource, load_completed_modules, modules_with_src
from .model import CompileNote, Conflict, SkeletonEntry, WiringSpec
from .skeleton import MergePlan, build_skeleton
from .wiring import build_wiring_specs, write_wiring_files


@dataclass
class MergeResult:
    """The full output of one merge run."""

    task_root: str
    modules: List[ModuleSource] = field(default_factory=list)
    graph: Optional[ModuleGraph] = None
    plan: MergePlan = field(default_factory=MergePlan)
    conflicts: List[Conflict] = field(default_factory=list)
    compile_notes: List[CompileNote] = field(default_factory=list)
    wiring_specs: List[WiringSpec] = field(default_factory=list)
    interface_files: List[str] = field(default_factory=list)
    wiring_files: List[str] = field(default_factory=list)
    db_path: Optional[str] = None
    warnings: List[str] = field(default_factory=list)

    # -- dsh.merge.skeleton (get) ------------------------------------------
    def skeleton(self) -> List[SkeletonEntry]:
        """Return skeleton entries (dirs then files) in placement order."""
        return self.plan.entries()

    def skeleton_json(self) -> List[dict]:
        return [e.to_dict() for e in self.skeleton()]

    # -- dsh.merge.conflicts (get) -----------------------------------------
    def conflicts_json(self) -> List[dict]:
        return [c.to_dict() for c in self.conflicts]

    # -- compile-readiness notes (非编译点明确说明) ---------------------------
    def compile_notes_json(self) -> List[dict]:
        return [n.to_dict() for n in self.compile_notes]

    # -- require/import wiring pins -----------------------------------------
    def wiring_json(self) -> List[dict]:
        return [w.to_dict() for w in self.wiring_specs]


class MergeEngine:
    """Runs the merge pipeline for a task root."""

    def __init__(
        self,
        depgraph_reader: Optional[DepGraphReader] = None,
    ) -> None:
        self.reader = depgraph_reader or DepGraphReader()

    def run(
        self,
        task_root: str,
        db_path: Optional[str] = None,
        output_root: Optional[str] = None,
        write_interfaces: bool = True,
    ) -> MergeResult:
        """Execute the pipeline.

        Args:
            task_root: directory containing ``modules/*`` (and possibly a
                codegraph database).
            db_path: explicit path to ``codegraph.db``.  When omitted the
                engine searches for a ``.codegraph/codegraph.db`` under the
                task root.
            output_root: where skeleton/interface outputs go.  Defaults to
                ``<task_root>/merge-output``.
            write_interfaces: whether to write per-module interface files.

        Returns:
            A :class:`MergeResult` carrying skeleton, conflicts and written
            interface-file paths.
        """
        task_root = os.path.abspath(task_root)
        output_root = os.path.abspath(output_root or os.path.join(task_root, "merge-output"))

        warnings: List[str] = []
        all_modules = load_completed_modules(task_root)
        modules = modules_with_src(task_root)

        # 1. dependency graph
        graph = ModuleGraph()
        resolved_db: Optional[str] = None
        if db_path:
            resolved_db = os.path.abspath(db_path)
        else:
            candidates = default_db_candidates(task_root)
            resolved_db = next((c for c in candidates if os.path.isfile(c)), None)
        if resolved_db:
            try:
                graph = self.reader.read(resolved_db)
            except Exception as exc:  # tolerate unreadable/invalid db
                warnings.append(
                    f"codegraph.db 读取失败({resolved_db}): {exc}; 改用模块 contract 依赖"
                )
                resolved_db = None
                graph = ModuleGraph()
        else:
            warnings.append(
                "未找到 codegraph.db, 使用模块 contract.yaml dependencies 构建依赖图"
            )
        # seed module files from discovered modules so they appear in graph
        for m in modules:
            graph.module_files.setdefault(m.id, set())
        # seed contract-derived deps for modules the graph knows nothing about
        for m in modules:
            if m.id not in graph.deps or not graph.deps[m.id]:
                graph.deps.setdefault(m.id, set()).update(m.deps)

        # 2. merged skeleton (tree-branch placement)
        plan = build_skeleton(modules, graph)

        # 3. conflicts (all four contract kinds)
        conflicts = aggregate_conflicts(modules, plan)

        # 3b. compile-readiness notes for non-compilable points
        compile_notes = build_compile_notes(modules, plan, conflicts)

        # 4. per-module target interface files + require/import wiring pins
        interface_files: List[str] = []
        wiring_files: List[str] = []
        wiring_specs: List[WiringSpec] = []
        if write_interfaces and modules:
            interface_files = write_interface_files(modules, graph, output_root)
            wiring_files = write_wiring_files(modules, graph, output_root)
            wiring_specs = build_wiring_specs(modules, graph, output_root)
            write_compile_notes(output_root, compile_notes)
        elif modules:
            interface_files = list_interface_files(output_root)

        return MergeResult(
            task_root=task_root,
            modules=modules,
            graph=graph,
            plan=plan,
            conflicts=conflicts,
            compile_notes=compile_notes,
            wiring_specs=wiring_specs,
            interface_files=interface_files,
            wiring_files=wiring_files,
            db_path=resolved_db,
            warnings=warnings,
        )


__all__ = ["MergeResult", "MergeEngine"]
