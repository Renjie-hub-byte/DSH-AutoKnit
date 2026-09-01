"""require/import wiring pins pointing at dependency interface files.

Each module's target interface file lives at
``<output_root>/interfaces/<module>/interface.json`` — a stable, addressable
path.  A module that *depends* on another needs wiring pins that make its
``require`` / ``import`` resolve to that interface file.

:func:`build_wiring_specs` produces one :class:`fw_merge.model.WiringSpec` per
module:

* ``requires`` — the interface-file path of every direct dependency (the
  require-pin for the module's wiring);
* ``imports``  — the concrete symbols the module imports from its dependencies,
  each resolved to the owning dependency's interface-file path (the
  import-pins).

Import statements are extracted from the module's Python sources with
:mod:`ast` (standard library, nothing executed).  A symbol resolves to a
dependency when that dependency declares it (per :mod:`fw_merge.signatures`) or
exposes it as an export.
"""

from __future__ import annotations

import ast
import json
import os
from typing import Dict, List, Tuple

from .depgraph import ModuleGraph
from .loader import ModuleSource
from .model import WiringSpec, normalize_interface_name
from .signatures import extract_signatures


def extract_imports(src_dir: str) -> List[Tuple[str, str]]:
    """Return ``(from_module, symbol)`` pairs imported across ``src_dir``.

    Handles both ``from mod import X`` and ``import mod`` (the latter resolved
    to its top-level module name).  Parsing failures are skipped.
    """
    out: List[Tuple[str, str]] = []
    files: List[str] = []
    for dirpath, _dirnames, filenames in os.walk(src_dir):
        for fn in filenames:
            if fn.endswith(".py"):
                files.append(os.path.join(dirpath, fn))
    for full in sorted(files):
        try:
            with open(full, "r", encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
        except (SyntaxError, OSError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    out.append((node.module, alias.name))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    out.append((top, top))
    return out


def _declared_symbol_modules(modules: List[ModuleSource]) -> Dict[str, str]:
    """Map a normalised symbol name to the module that declares it (first)."""
    declared: Dict[str, str] = {}
    for mod in sorted(modules, key=lambda m: m.id):
        for sym in extract_signatures(mod.src_dir):
            norm = normalize_interface_name(sym)
            declared.setdefault(norm, mod.id)
        for export in mod.exports:
            declared.setdefault(normalize_interface_name(export), mod.id)
    return declared


def _interface_file(output_root: str, module_id: str) -> str:
    return os.path.join(output_root, "interfaces", module_id, "interface.json")


def build_wiring_specs(
    modules: List[ModuleSource], graph: ModuleGraph, output_root: str
) -> List[WiringSpec]:
    """Compute a :class:`WiringSpec` per module against its dependency interfaces.

    Args:
        modules: completed module sources.
        graph: module dependency graph.
        output_root: root under which ``interfaces/`` already exists (written by
            :mod:`fw_merge.interfaces`).

    Returns:
        Sorted list of wiring specs (one per module).
    """
    declared = _declared_symbol_modules(modules)
    specs: List[WiringSpec] = []
    for mod in sorted(modules, key=lambda m: m.id):
        deps = set(graph.deps.get(mod.id, set())) or set(mod.deps)

        requires: List[str] = []
        for dep in sorted(deps):
            iface = _interface_file(output_root, dep)
            if os.path.isfile(iface):
                requires.append(iface)

        imports: List[Dict[str, str]] = []
        for from_mod, symbol in extract_imports(mod.src_dir):
            target = declared.get(normalize_interface_name(symbol))
            if target is None or target not in deps:
                continue
            imports.append(
                {
                    "symbol": symbol,
                    "from": target,
                    "interface_file": _interface_file(output_root, target),
                }
            )

        specs.append(
            WiringSpec(
                module=mod.id,
                requires=sorted(set(requires)),
                imports=sorted(
                    imports, key=lambda d: (d["symbol"], d["from"], d["interface_file"])
                ),
            )
        )
    return specs


def write_wiring_files(
    modules: List[ModuleSource], graph: ModuleGraph, output_root: str
) -> List[str]:
    """Write ``<output_root>/wiring/<module>/wiring.json`` for each module.

    Returns:
        Absolute paths of the wiring files written.
    """
    written: List[str] = []
    for spec in build_wiring_specs(modules, graph, output_root):
        directory = os.path.join(output_root, "wiring", spec.module)
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, "wiring.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(spec.to_dict(), ensure_ascii=False, indent=2))
            fh.write("\n")
        written.append(path)
    return written


__all__ = [
    "extract_imports",
    "build_wiring_specs",
    "write_wiring_files",
]
