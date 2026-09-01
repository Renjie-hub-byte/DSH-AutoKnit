"""Loader for *completed* module directories under a task root.

A completed module is one that has produced a usable ``src/`` tree (and
optionally a ``contract.yaml`` describing its id / declared interface /
dependencies).  The loader scans ``<task_root>/modules/*`` and builds a list of
:class:`ModuleSource` objects consumed by the merge skeleton builder.

It uses only the standard library plus :mod:`pyyaml` for parsing the optional
``contract.yaml``; nothing is executed from the modules themselves.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .depgraph import slug_module

#: Sub-directory inside a module that holds its code.
SRC_DIR = "src"

#: Marker file carrying a module's declared public interface.
INTERFACE_MARKER = "interface.json"

#: Marker file carrying module metadata (id, dependencies, declared interface).
CONTRACT_FILE = "contract.yaml"


@dataclass
class ModuleSource:
    """A discovered, completed module.

    Attributes:
        id: stable module id (e.g. ``"m02"`` from ``"m02-程序化合代码 merge"``).
        dirname: the directory name on disk (the full slug).
        root: absolute path of the module directory.
        src_dir: absolute path of the module's code tree.
        deps: dependency module ids parsed from contract.yaml (may be empty).
        interface_name: declared public interface name (from marker or derived).
        exports: public files/symbols declared by the module (may be empty).
    """

    id: str
    dirname: str
    root: str
    src_dir: str
    deps: List[str] = field(default_factory=list)
    interface_name: str = ""
    exports: List[str] = field(default_factory=list)

    def has_src(self) -> bool:
        """True when the module carries a non-empty code tree."""
        return os.path.isdir(self.src_dir) and bool(
            list(os.scandir(self.src_dir))
        )


def find_module_dirs(task_root: str) -> List[str]:
    """Return absolute paths of module directories under ``task_root/modules``.

    Only directories are returned; the ``modules`` directory itself may not
    exist, in which case an empty list is returned.
    """
    root = os.path.abspath(task_root)
    modules_dir = os.path.join(root, "modules")
    if not os.path.isdir(modules_dir):
        return []
    return [
        os.path.join(modules_dir, d)
        for d in sorted(os.listdir(modules_dir))
        if os.path.isdir(os.path.join(modules_dir, d))
    ]


def _read_yaml(path: str) -> Dict:
    import yaml  # local import keeps loader lazy

    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _parse_deps(contract: Dict) -> List[str]:
    """Extract dependency module ids from a contract dict."""
    raw = contract.get("dependencies", []) or []
    if isinstance(raw, str):
        raw = [raw]
    out = []
    for item in raw:
        if isinstance(item, dict):
            item = item.get("id") or item.get("name") or ""
        s = str(item)
        # a dependency entry may be "m03-...", take leading token
        m = re.match(r"^([a-zA-Z0-9_\-]+)", s.strip())
        if m:
            out.append(m.group(1))
    return out


def _parse_exports(contract: Dict) -> List[str]:
    """Extract the public interface paths a module declares in its contract.

    These come from the ``read_api`` entries (e.g. ``dsh.merge.conflicts``)
    which are the wiring handles the module exposes to others.
    """
    exports: List[str] = []
    for key in ("read_api", "interfaces"):
        entries = contract.get(key, []) or []
        if isinstance(entries, dict):
            entries = [entries]
        for entry in entries:
            if isinstance(entry, dict):
                path = entry.get("path")
                if path:
                    exports.append(str(path))
    return exports


def load_module_source(module_dir: str) -> ModuleSource:
    """Build a :class:`ModuleSource` for one module directory.

    Never raises on missing files: any optional metadata that cannot be read
    simply falls back to a derived default.
    """
    dirname = os.path.basename(module_dir)
    mid = slug_module(dirname)
    src_dir = os.path.join(module_dir, SRC_DIR)

    contract = _read_yaml(os.path.join(module_dir, CONTRACT_FILE))
    deps = _parse_deps(contract)
    exports = _parse_exports(contract)

    # interface name: declared in interface.json marker, else derived from id
    interface_name = _read_interface_name(module_dir) or mid

    return ModuleSource(
        id=mid,
        dirname=dirname,
        root=os.path.abspath(module_dir),
        src_dir=os.path.abspath(src_dir),
        deps=deps,
        interface_name=interface_name,
        exports=exports,
    )


def _read_interface_name(module_dir: str) -> Optional[str]:
    """Read the declared interface name from ``interface.json`` if present."""
    marker = os.path.join(module_dir, INTERFACE_MARKER)
    if not os.path.isfile(marker):
        return None
    import json

    try:
        with open(marker, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return None
    if isinstance(data, dict):
        return data.get("name") or data.get("interface")
    return None


def load_completed_modules(task_root: str) -> List[ModuleSource]:
    """Load all completed modules under ``task_root``.

    A module counts as *completed* when it has a non-empty ``src/`` tree.
    Modules that exist but produced nothing are still returned but flagged by
    ``has_src() == False`` so the caller can decide how to handle them.
    """
    return [load_module_source(d) for d in find_module_dirs(task_root)]


def modules_with_src(task_root: str) -> List[ModuleSource]:
    """Load modules and keep only those with a non-empty ``src/`` tree."""
    return [m for m in load_completed_modules(task_root) if m.has_src()]


__all__ = [
    "ModuleSource",
    "SRC_DIR",
    "INTERFACE_MARKER",
    "CONTRACT_FILE",
    "find_module_dirs",
    "load_module_source",
    "load_completed_modules",
    "modules_with_src",
]
