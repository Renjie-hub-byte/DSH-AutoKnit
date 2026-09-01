"""Per-module target interface file generation.

For every completed module we write a *target interface file* that declares
the module's public wiring surface: its interface name, the exports it exposes
to other modules, and the modules it depends on.  Downstream blocks use these
files as the wiring pins ("接线钉") for require/import binding.

The interface file is the JSON form of :class:`fw_merge.model.InterfaceSpec`:

    {
      "name": "<interface name>",
      "module": "<module id>",
      "exports": ["dsh.merge.conflicts", ...],
      "deps": ["m02", ...]
    }

Files are written under ``<output_root>/interfaces/<module_id>/interface.json``
so each module's target interface is addressable by a stable path.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List

from .depgraph import ModuleGraph
from .loader import ModuleSource
from .model import InterfaceSpec


def build_interface_specs(
    modules: List[ModuleSource], graph: ModuleGraph
) -> List[InterfaceSpec]:
    """Compute an :class:`InterfaceSpec` per completed module.

    Dependencies come from the module graph when known, otherwise from the
    module's own contract.yaml ``dependencies``.
    """
    known_deps = graph.deps
    specs: List[InterfaceSpec] = []
    for mod in sorted(modules, key=lambda m: m.id):
        deps = known_deps.get(mod.id)
        if not deps:
            deps = set(mod.deps)
        specs.append(
            InterfaceSpec(
                name=mod.interface_name,
                module=mod.id,
                exports=sorted(set(mod.exports)) or [],
                deps=sorted(deps),
            )
        )
    return specs


def write_interface_files(
    modules: List[ModuleSource],
    graph: ModuleGraph,
    output_root: str,
) -> List[str]:
    """Write a target interface file for each module.

    Args:
        modules: completed module sources.
        graph: module dependency graph.
        output_root: root under which ``interfaces/<module>/interface.json``
            files are created.

    Returns:
        Absolute paths of the interface files written.
    """
    specs = build_interface_specs(modules, graph)
    written: List[str] = []
    for spec in specs:
        directory = os.path.join(output_root, "interfaces", spec.module)
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, "interface.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(
                spec.to_dict(), fh, ensure_ascii=False, indent=2
            )
            fh.write("\n")
        written.append(path)
    return written


def list_interface_files(output_root: str) -> List[str]:
    """Return absolute paths of all interface files already under output_root."""
    root = os.path.join(output_root, "interfaces")
    if not os.path.isdir(root):
        return []
    files: List[str] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in sorted(filenames):
            if fn == "interface.json":
                files.append(os.path.join(dirpath, fn))
    return sorted(files)


__all__ = [
    "build_interface_specs",
    "write_interface_files",
    "list_interface_files",
]
