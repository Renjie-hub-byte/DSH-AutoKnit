"""Dependency-graph reader for fw-merge.

Reads the codegraph database produced by the codegraph tool
(``framework-v1/.codegraph/codegraph.db``) using only the Python standard
library ``sqlite3`` module, then builds a *module-level* dependency graph.

The schema of a codegraph database is not standardised across tool versions,
so :class:`DepGraphReader` performs tolerant schema discovery:

* it locates a *node* table (rows that name a file/symbol and may carry a
  module affiliation) and an *edge* table (rows expressing a
  ``src -> dst`` dependency);
* it derives each node's owning module from a ``module`` column when present,
  otherwise from the node's ``path`` by looking for the ``modules/`` marker;
* it folds node edges up to module edges: module A depends on module B when
  any node owned by A has an edge to any node owned by B.

The resulting :class:`DepGraph` exposes a deterministic *topological* merge
order (dependencies laid down first) and a *tree-branch* grouping used by the
directory-placement step.
"""

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

# --------------------------------------------------------------------------
# Schema discovery
# --------------------------------------------------------------------------

# Preferred table names, in priority order, for nodes and edges.
_NODE_TABLE_PRIORITY = ("nodes", "files", "symbols", "entities")
_EDGE_TABLE_PRIORITY = ("edges", "dependencies", "relations", "refs")

# Candidate column names for the "source module" of a node.
_MODULE_COLUMNS = ("module", "module_id", "module_name", "namespace")

# Candidate column names for the path of a node.
_PATH_COLUMNS = ("path", "file", "file_path", "name", "qualified_name")

# Candidate column names for the endpoints of an edge.
_SRC_COLUMNS = ("src", "src_id", "source", "source_id", "from", "from_id")
_DST_COLUMNS = ("dst", "dst_id", "target", "target_id", "to", "to_id")

#: A table/column name must be a plain identifier before it is interpolated
#: into a SQL string.  Codegraph DB names are introspected from ``sqlite_master``
#: / ``PRAGMA table_info`` (not supplied by end users), but rejecting anything
#: that is not ``[A-Za-z_][A-Za-z0-9_]*`` keeps the SQL well-formed regardless.
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _safe_ident(name: str) -> Optional[str]:
    """Return ``name`` when it is a safe SQL identifier, else ``None``."""
    if isinstance(name, str) and _IDENT_RE.match(name):
        return name
    return None


@dataclass
class Node:
    """A node in the codegraph (typically a file or symbol)."""

    node_id: str
    name: str
    path: str = ""
    module: str = ""


@dataclass
class ModuleGraph:
    """Module-level dependency graph derived from a codegraph DB.

    Attributes:
        nodes: node_id -> :class:`Node` (all raw nodes read from the DB).
        module_files: module_id -> set of node names/paths owned by it.
        deps: module_id -> set of module_ids it directly depends on.
    """

    nodes: Dict[str, Node] = field(default_factory=dict)
    module_files: Dict[str, Set[str]] = field(default_factory=dict)
    deps: Dict[str, Set[str]] = field(default_factory=dict)

    # -- module helpers -----------------------------------------------------
    def modules(self) -> List[str]:
        """Sorted list of module ids present in the graph."""
        return sorted(self.module_files.keys())

    def add_dep(self, src: str, dst: str) -> None:
        """Record that module ``src`` depends on module ``dst``."""
        if src == dst:
            return
        self.module_files.setdefault(src, set())
        self.module_files.setdefault(dst, set())
        self.deps.setdefault(src, set()).add(dst)

    def direct_deps(self, module: str) -> List[str]:
        """Sorted direct dependencies of ``module``."""
        return sorted(self.deps.get(module, set()))

    def topological_order(self) -> List[str]:
        """Modules in a deterministic dependency-first (topological) order.

        Dependencies are emitted before dependents.  When several modules are
        ready at the same time they are emitted in lexicographic module order
        so the result is reproducible.  Cycles are tolerated by emitting the
        remaining modules in lexicographic order at the end.
        """
        mods = set(self.module_files.keys())
        remaining = set(mods)
        result: List[str] = []

        # in-degree = number of dependencies *not yet emitted*
        indeg: Dict[str, int] = {
            m: sum(1 for d in self.deps.get(m, set()) if d in remaining)
            for m in mods
        }

        ready = sorted(m for m in mods if indeg[m] == 0)
        while ready:
            cur = ready.pop(0)
            if cur not in remaining:
                continue
            remaining.discard(cur)
            result.append(cur)
            for d in self.deps.get(cur, set()):
                if d in remaining:
                    indeg[d] -= 1
                    if indeg[d] == 0:
                        ready.append(d)
            ready = sorted(set(ready))

        # Any modules left (cycle) are appended deterministically.
        if remaining:
            result.extend(sorted(remaining))
        return result

    def branches(self) -> List[List[str]]:
        """Group modules into *tree branches* following the dependency graph.

        A branch is the set of modules reachable from a *root* module (a
        module nobody depends on) by following dependency edges backwards
        (i.e. the root together with everything it transitively depends on).

        Returns:
            List of branches, each a list of modules in topological order.
            Branches are ordered by their root module id.
        """
        roots = sorted(
            m for m in self.module_files
            if not any(m in dsts for dsts in self.deps.values())
        )
        order = self.topological_order()
        # If there is exactly one connected component the whole graph is one
        # branch; fall back to treating each module as its own root.
        if not roots:
            roots = sorted(self.module_files.keys())

        branches: List[List[str]] = []
        for root in roots:
            # reachable = root + everything it transitively depends on
            reach = set()
            stack = [root]
            while stack:
                cur = stack.pop()
                if cur in reach:
                    continue
                reach.add(cur)
                for d in self.deps.get(cur, set()):
                    stack.append(d)
            branch = [m for m in order if m in reach]
            branches.append(branch)
        return branches


def _derive_module_from_path(path: str, module_roots: Sequence[str]) -> Optional[str]:
    """Guess a node's module from a path such as ``modules/m02-.../src/x.py``.

    Looks for the first segment after a ``modules`` marker and returns its
    module id (up to the first ``-`` when present).
    """
    if not path:
        return None
    norm = path.replace("\\", "/")
    for marker in module_roots:
        idx = norm.find(marker + "/")
        if idx < 0:
            idx = norm.find(marker + "\\")
        if idx >= 0:
            rest = norm[idx + len(marker) + 1:]
            first = rest.split("/", 1)[0]
            return slug_module(first)
    return None


def slug_module(name: str) -> str:
    """Turn a module directory name into a stable module id.

    ``"m02-程序化合代码 merge"`` -> ``"m02"``, ``"mod_a"`` -> ``"mod_a"``.
    """
    name = name.strip().strip("/")
    if not name:
        return name
    m = re.match(r"^([a-zA-Z0-9_\-]+)", name)
    if not m:
        return name
    return m.group(1)


def _first_existing(available: set, candidates: Sequence[str]) -> Optional[str]:
    """Return the first column name in ``candidates`` present in ``available``."""
    for c in candidates:
        if c in available:
            return c
    return None


class DepGraphReader:
    """Reads a codegraph.sqlite database and produces a :class:`ModuleGraph`.

    The reader is intentionally lenient: it discovers node/edge tables and
    column names rather than hard-coding a single schema, so it survives
    across codegraph versions.  If a table/column cannot be found the graph is
    built from whatever *is* present (possibly only node information).
    """

    def __init__(
        self,
        module_roots: Sequence[str] = ("modules",),
        node_table_priority: Sequence[str] = _NODE_TABLE_PRIORITY,
        edge_table_priority: Sequence[str] = _EDGE_TABLE_PRIORITY,
    ) -> None:
        self.module_roots = tuple(module_roots) or ("modules",)
        self.node_table_priority = tuple(node_table_priority)
        self.edge_table_priority = tuple(edge_table_priority)

    # -- public API ---------------------------------------------------------
    def read(self, db_path: str) -> ModuleGraph:
        """Open ``db_path`` and build a :class:`ModuleGraph`.

        Raises:
            FileNotFoundError: if the database file does not exist.
            sqlite3.DatabaseError: if the file is not a valid SQLite database.
        """
        if not os.path.isfile(db_path):
            raise FileNotFoundError(f"codegraph database not found: {db_path}")
        with sqlite3.connect(db_path) as conn:
            schema = self._schema(conn)
            graph = self._build(conn, schema)
        return graph

    # -- schema introspection ----------------------------------------------
    def _schema(self, conn: sqlite3.Connection) -> Dict:
        """Discover node/edge tables and their columns."""
        tables = self._tables(conn)
        node_table = self._pick_table(tables, self.node_table_priority)
        edge_table = self._pick_table(tables, self.edge_table_priority)
        return {
            "tables": tables,
            "node_table": node_table,
            "edge_table": edge_table,
            "node_cols": set(self._columns(conn, node_table)) if node_table else set(),
            "edge_cols": set(self._columns(conn, edge_table)) if edge_table else set(),
        }

    @staticmethod
    def _tables(conn: sqlite3.Connection) -> List[str]:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
        ).fetchall()
        return [r[0] for r in rows]

    @staticmethod
    def _columns(conn: sqlite3.Connection, table: str) -> List[str]:
        if not _safe_ident(table):
            return []
        # nosemgrep
        return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()]

    @staticmethod
    def _pick_table(tables: List[str], priority: Sequence[str]) -> Optional[str]:
        for cand in priority:
            if cand in tables:
                return cand
        # fall back: first table whose name hints at the role
        for t in tables:
            if t in _NODE_TABLE_PRIORITY or t in _EDGE_TABLE_PRIORITY:
                return t
        return None

    # -- build --------------------------------------------------------------
    def _build(self, conn: sqlite3.Connection, schema: Dict) -> ModuleGraph:
        graph = ModuleGraph()
        # Only interpolate identifiers that are provably safe SQL identifiers.
        node_table = _safe_ident(schema["node_table"])
        edge_table = _safe_ident(schema["edge_table"])

        # --- nodes ---
        mod_col = _first_existing(schema["node_cols"], _MODULE_COLUMNS) if node_table else None
        path_col = _first_existing(schema["node_cols"], _PATH_COLUMNS) if node_table else None

        if node_table:
            id_cols = [
                c for c in ("id", "node_id", "rowid") if c in schema["node_cols"]
            ]
            id_col = id_cols[0] if id_cols else None
            name_col = _first_existing(schema["node_cols"], ("name",)) or path_col
            # nosemgrep
            for row in conn.execute(f'SELECT * FROM "{node_table}"').fetchall():
                rec = self._row_dict(conn, node_table, row)
                node = self._make_node(rec, id_col, name_col, path_col, mod_col)
                if node.node_id:
                    graph.nodes[node.node_id] = node
                if node.module:
                    graph.module_files.setdefault(node.module, set()).add(
                        node.path or node.name
                    )
        else:
            # No node table: nothing we can attribute. A module graph with no
            # modules is still valid (used for the empty-db test).
            pass

        # --- edges ---
        if edge_table and node_table:
            src_col = _first_existing(schema["edge_cols"], _SRC_COLUMNS)
            dst_col = _first_existing(schema["edge_cols"], _DST_COLUMNS)
            if src_col and dst_col:
                # nosemgrep
                for row in conn.execute(f'SELECT * FROM "{edge_table}"').fetchall():
                    rec = self._row_dict(conn, edge_table, row)
                    src_id = str(rec.get(src_col, ""))
                    dst_id = str(rec.get(dst_col, ""))
                    src_mod = self._module_of(graph, src_id)
                    dst_mod = self._module_of(graph, dst_id)
                    if src_mod and dst_mod:
                        graph.add_dep(src_mod, dst_mod)
        return graph

    def _make_node(
        self,
        rec: Dict,
        id_col: Optional[str],
        name_col: Optional[str],
        path_col: Optional[str],
        mod_col: Optional[str],
    ) -> Node:
        node_id = str(rec.get(id_col, "")) if id_col else ""
        if not node_id and name_col:
            node_id = str(rec.get(name_col, ""))
        name = str(rec.get(name_col, "")) if name_col else ""
        path = str(rec.get(path_col, "")) if path_col else ""
        module = str(rec.get(mod_col, "")) if mod_col else ""
        if not module:
            module = _derive_module_from_path(path, self.module_roots) or ""
        return Node(node_id=node_id, name=name, path=path, module=module)

    def _module_of(self, graph: ModuleGraph, node_id: str) -> Optional[str]:
        node = graph.nodes.get(node_id)
        return node.module if node else None

    @staticmethod
    def _row_dict(conn: sqlite3.Connection, table: str, row: Tuple) -> Dict:
        cols = DepGraphReader._columns(conn, table)
        return dict(zip(cols, row))


def default_db_candidates(task_root: str) -> List[str]:
    """Return likely codegraph DB paths under ``task_root``.

    The canonical location referenced by the task book is
    ``<task_root>/framework-v1/.codegraph/codegraph.db``.  Any
    ``**/.codegraph/codegraph.db`` is also a candidate.
    """
    candidates = []
    root = os.path.abspath(task_root)
    candidates.append(os.path.join(root, "framework-v1", ".codegraph", "codegraph.db"))
    candidates.append(os.path.join(root, ".codegraph", "codegraph.db"))
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__")]
        if os.path.basename(dirpath) == ".codegraph" and "codegraph.db" in filenames:
            candidates.append(os.path.join(dirpath, "codegraph.db"))
    # de-duplicate, keep order
    seen, out = set(), []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


__all__ = [
    "Node",
    "ModuleGraph",
    "DepGraphReader",
    "default_db_candidates",
    "slug_module",
]
