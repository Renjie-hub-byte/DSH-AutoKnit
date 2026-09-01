"""Merged directory-skeleton builder.

This module performs the *directory placement* ("目录归位") of many completed
executor module trees into a single target tree, ordered along the
dependency graph's *tree-branch* dimension.

Algorithm
---------
1. Modules are processed in :meth:`ModuleGraph.topological_order` — i.e.
   *dependencies first*.  This lays down shared/upstream code before
   downstream code so a downstream module can overlay on top of the code it
   depends on (the "tree-branch" merge order).
2. Each module's ``src/`` tree is overlaid onto the target root: a directory
   entry's target path is its path relative to the module's ``src/`` root; the
   owning module is the first one (in merge order) to provide that path.
3. Directories simply combine (unions are conflict-free).  Files that two
   modules place at the *same* target path are a ``same_name`` conflict — the
   dependency-first provider owns the placement, later providers are held
   aside and flagged for a human decision.

Nothing here executes module code; only file/directory layout is considered.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple

from .depgraph import ModuleGraph
from .loader import ModuleSource
from .model import Conflict, SkeletonEntry

#: Files that are safe to overlay without a conflict even when duplicated
#: across modules (package marker / empty init).  Configurable via constructor.
DEFAULT_BENIGN_BASENAMES = {"__init__.py", "__init__.pyi", ".gitkeep", ".DS_Store"}


@dataclass
class MergePlan:
    """Result of placing every module's files into one target skeleton.

    Attributes:
        skeleton: ordered list of :class:`SkeletonEntry` (dirs then files).
        conflicts: ``same_name`` conflicts discovered while placing files.
        target_path_owner: target_path -> owning module id (the placed one).
        held_aside: mapping target_path -> list of module ids that wanted the
            path but lost the placement (pending human decision).
    """

    skeleton: List[SkeletonEntry] = field(default_factory=list)
    conflicts: List[Conflict] = field(default_factory=list)
    target_path_owner: Dict[str, str] = field(default_factory=dict)
    held_aside: Dict[str, List[str]] = field(default_factory=dict)

    def entries(self) -> List[SkeletonEntry]:
        return list(self.skeleton)


def _iter_source_files(src_dir: str) -> List[str]:
    """Return all file paths under ``src_dir`` (relative, posix separators)."""
    files: List[str] = []
    for dirpath, dirnames, filenames in os.walk(src_dir):
        # deterministic traversal
        dirnames.sort()
        filenames.sort()
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, src_dir).replace(os.sep, "/")
            files.append(rel)
    return files


def build_skeleton(
    modules: List[ModuleSource],
    graph: ModuleGraph,
    benign_basenames: Set[str] = DEFAULT_BENIGN_BASENAMES,
) -> MergePlan:
    """Build the merged skeleton from module sources in dependency order.

    Args:
        modules: completed module sources (those with ``has_src()``).
        graph: module dependency graph (may be empty).
        benign_basenames: basenames that never trigger a ``same_name``
            conflict when duplicated.

    Returns:
        A :class:`MergePlan` with the placed skeleton and same-name conflicts.
    """
    plan = MergePlan()
    benign = set(benign_basenames) or DEFAULT_BENIGN_BASENAMES

    # Merge order: dependency-first topological order over the graph, then any
    # module the graph does not know about appended in module-id order.
    known = set(graph.modules())
    order = graph.topological_order()
    ordered_ids = [m for m in order if m in known]
    remaining_ids = sorted(m.id for m in modules if m.id not in known)
    ordered_ids += [m for m in remaining_ids if m not in ordered_ids]
    # keep only modules actually present
    present = {m.id: m for m in modules}
    ordered_modules = [present[i] for i in ordered_ids if i in present]

    # Directories are placed once, owned by the first provider in merge order.
    placed_dirs: Dict[str, str] = {}
    placed_files: Dict[str, str] = {}

    for mod in ordered_modules:
        files = _iter_source_files(mod.src_dir)
        # Emit directory entries (deduplicated, owner = first provider).
        dirset: Set[str] = set()
        for rel in files:
            parts = rel.split("/")
            acc = ""
            for p in parts[:-1]:
                acc = f"{acc}/{p}" if acc else p
                dirset.add(acc)
        for d in sorted(dirset):
            if d not in placed_dirs:
                placed_dirs[d] = mod.id
                plan.skeleton.append(
                    SkeletonEntry(target_path=d, source_module=mod.id, kind="dir")
                )

        # Place files, detecting same-name conflicts.
        for rel in files:
            base = rel.rsplit("/", 1)[-1]
            if rel in placed_files:
                # existing placement -> potential conflict
                if base in benign:
                    continue  # benign duplicate, keep existing placement
                prior = placed_files[rel]
                conflict = Conflict(
                    kind="same_name",
                    module_refs=[prior, mod.id],
                    description=(
                        f"两个模块都把文件放到目标路径 {rel!r}: "
                        f"'{prior}' 先放置, '{mod.id}' 也提供同路径文件; "
                        f"需人工决定保留哪一份或改写路径。"
                    ),
                    needs_human=True,
                )
                plan.conflicts.append(conflict)
                plan.held_aside.setdefault(rel, []).append(mod.id)
                continue
            placed_files[rel] = mod.id
            plan.skeleton.append(
                SkeletonEntry(target_path=rel, source_module=mod.id, kind="file")
            )

    plan.target_path_owner = dict(placed_dirs)
    plan.target_path_owner.update(placed_files)
    # deterministic ordering of conflicts (by kind then refs then description)
    plan.conflicts.sort(
        key=lambda c: (c.kind, tuple(c.module_refs), c.description)
    )
    return plan


__all__ = ["MergePlan", "build_skeleton", "DEFAULT_BENIGN_BASENAMES"]
