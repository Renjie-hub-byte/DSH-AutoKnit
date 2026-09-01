"""Core data model for the fw-merge module.

These dataclasses are the single source of truth for the shapes that cross
the module boundary.  They mirror the ``data_shape`` declared in
``contract.yaml`` / ``任务书-m02.yaml``:

* ``dsh.merge.conflicts``  -> list of :class:`Conflict`
* ``dsh.merge.skeleton``   -> list of :class:`SkeletonEntry`

Keeping the model here lets the CLI, the ``dsh.*`` get-handlers and the tests
all agree on field names and JSON serialization without drift.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

# --------------------------------------------------------------------------
# Enums (aligned with data_contract.shared_enums.merge_conflict_kind)
# --------------------------------------------------------------------------
#: The four merge-conflict kinds recognised by the framework contract.
#: This block (v1) only *detects* ``same_name`` and ``naming_conflict``;
#: ``signature_mismatch`` and ``semantic_merge`` are reserved for later blocks
#: but kept in the enum so downstream code can already branch on them.
MERGE_CONFLICT_KINDS = (
    "same_name",
    "naming_conflict",
    "signature_mismatch",
    "semantic_merge",
)

#: Directory-skeleton entry kinds.
SKELETON_KINDS = ("dir", "file")


@dataclass
class SkeletonEntry:
    """One line of the merged directory skeleton (dsh.merge.skeleton).

    Attributes:
        target_path: path the entry is placed at in the merged tree, relative
            to the merge output root.
        source_module: module id that contributes this entry.
        kind: ``dir`` or ``file``.
    """

    target_path: str
    source_module: str
    kind: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_path": self.target_path,
            "source_module": self.source_module,
            "kind": self.kind,
        }


@dataclass
class Conflict:
    """One entry of the wiring-conflict list (dsh.merge.conflicts).

    Attributes:
        kind: one of :data:`MERGE_CONFLICT_KINDS`.
        module_refs: modules involved in the conflict (the modules whose
            outputs collide).
        description: human-readable explanation.
        needs_human: True when an automated merge cannot resolve the item and
            a human/agent decision is required.
    """

    kind: str
    module_refs: List[str]
    description: str
    needs_human: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "module_refs": list(self.module_refs),
            "description": self.description,
            "needs_human": self.needs_human,
        }


@dataclass
class CompileNote:
    """An explicit explanation of a non-compilable point in the merged skeleton.

    The tool never forces the merged skeleton to compile (per the task's
    acceptance: either compile it, *or* clearly explain what cannot compile).
    Each :class:`CompileNote` ties a non-compilable point back to the module,
    the offending target path and the conflict kind that caused it.

    Attributes:
        module: module id affected by the non-compilable point.
        compiles: always ``False`` here (this entry exists because it does not
            compile); kept as a field so consumers can distinguish notes.
        reason: the conflict kind responsible (one of ``MERGE_CONFLICT_KINDS``).
        target_path: target path that cannot compile (empty when the failure
            is not tied to a single path, e.g. a naming/signature conflict).
        explanation: why it cannot compile and what a human should do.
    """

    module: str
    compiles: bool = False
    reason: str = ""
    target_path: str = ""
    explanation: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "module": self.module,
            "compiles": self.compiles,
            "reason": self.reason,
            "target_path": self.target_path,
            "explanation": self.explanation,
        }


@dataclass
class WiringSpec:
    """A module's require/import wiring pins pointing to dependency interfaces.

    Written to ``<output_root>/wiring/<module>/wiring.json``.  ``requires``
    lists the interface-file paths of every direct dependency; ``imports``
    lists the concrete symbols a module imports and which dependency's
    interface file resolves them.

    Attributes:
        module: owning module id.
        requires: absolute interface-file paths of direct dependencies.
        imports: list of ``{"symbol", "from", "interface_file"}`` dicts.
    """

    module: str
    requires: List[str] = field(default_factory=list)
    imports: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "module": self.module,
            "requires": list(self.requires),
            "imports": [dict(i) for i in self.imports],
        }


@dataclass
class InterfaceSpec:
    """A module's public interface declaration, written to its target
    interface file.

    Attributes:
        name: interface name (the wiring handle importers bind to).
        module: owning module id.
        exports: public symbols / files the module exposes.
        deps: modules this module depends on (from the dependency graph).
    """

    name: str
    module: str
    exports: List[str] = field(default_factory=list)
    deps: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "module": self.module,
            "exports": list(self.exports),
            "deps": list(self.deps),
        }


def normalize_interface_name(name: str) -> str:
    """Normalise an interface name for case/separator-insensitive comparison.

    ``"orderService"``, ``"orderservice"`` and ``"order_service"`` all
    normalise to ``"orderservice"``.  Two interfaces that are *not* byte equal
    but *are* normalisation equal are reported as a ``naming_conflict``
    (the same logical interface spelled differently).
    """
    if not isinstance(name, str):
        name = str(name)
    return "".join(ch for ch in name if ch.isalnum()).lower()


def to_json_bytes(items: List[Any]) -> bytes:
    """Serialise a list of model objects (or dicts) to compact JSON bytes."""
    payload = [it.to_dict() if hasattr(it, "to_dict") else it for it in items]
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def dumps(items: List[Any]) -> str:
    """Serialise a list of model objects to a compact JSON string."""
    return to_json_bytes(items).decode("utf-8")


__all__ = [
    "MERGE_CONFLICT_KINDS",
    "SKELETON_KINDS",
    "SkeletonEntry",
    "Conflict",
    "CompileNote",
    "WiringSpec",
    "InterfaceSpec",
    "to_json_bytes",
    "dumps",
    "normalize_interface_name",
]
