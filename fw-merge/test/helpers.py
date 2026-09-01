"""Shared test helpers: build sample task trees and codegraph DBs."""

from __future__ import annotations

import os
import sqlite3

# ---------------------------------------------------------------------------
# codegraph.db builder
# ---------------------------------------------------------------------------


def build_codegraph_db(
    db_path,
    nodes=(),
    edges=(),
    node_cols=("id", "name", "path", "module"),
    edge_cols=("id", "src_id", "dst_id"),
):
    """Create a SQLite codegraph DB with ``nodes`` and ``edges`` tables.

    ``nodes``: iterable of row tuples matching ``node_cols``.
    ``edges``: iterable of (src_id, dst_id) row tuples.
    """
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(f'CREATE TABLE nodes ({", ".join(node_cols)})')
        conn.execute(f'CREATE TABLE edges ({", ".join(edge_cols)})')
        for n in nodes:
            conn.execute("INSERT INTO nodes VALUES (?,?,?,?)", n)
        for i, (s, d) in enumerate(edges):
            conn.execute("INSERT INTO edges VALUES (?,?,?)", (i, s, d))
        conn.commit()
    finally:
        conn.close()
    return db_path



# ---------------------------------------------------------------------------
# sample task tree
# ---------------------------------------------------------------------------

SAMPLE_EDGES = [
    # mod_b depends on mod_a (edge from a mod_b node to a mod_a node)
    ("mod_b/bar/order.py", "mod_a/foo/util.py"),
    ("mod_b/foo/util.py", "mod_a/foo/util.py"),
]


def build_sample_task(root):
    """Create the canonical sample task and return its root path.

    Layout::

        <root>/
          modules/
            mod_a/{src/foo/{__init__.py, util.py}, contract.yaml, interface.json}
            mod_b/{src/{bar/order.py, foo/util.py}, contract.yaml, interface.json}
            mod_c/{src/__init__.py, contract.yaml, interface.json}
          framework-v1/.codegraph/codegraph.db
    """
    root = os.path.abspath(root)
    mods = os.path.join(root, "modules")

    _write(os.path.join(mods, "mod_a", "src", "foo", "__init__.py"), "")
    _write(os.path.join(mods, "mod_a", "src", "foo", "util.py"), "def helper(): ...\n")
    _write(
        os.path.join(mods, "mod_a", "contract.yaml"),
        "module: mod_a\ndependencies: []\nread_api:\n  - path: dsh.orders.fetch\n",
    )
    _write(
        os.path.join(mods, "mod_a", "interface.json"),
        '{"name": "orderService", "exports": ["dsh.orders.fetch"]}\n',
    )

    _write(os.path.join(mods, "mod_b", "src", "bar", "order.py"), "class Order: ...\n")
    _write(os.path.join(mods, "mod_b", "src", "foo", "util.py"), "def helper(): ...\n")
    _write(
        os.path.join(mods, "mod_b", "contract.yaml"),
        "module: mod_b\ndependencies: [mod_a]\nread_api:\n  - path: dsh.orders.submit\n",
    )
    _write(
        os.path.join(mods, "mod_b", "interface.json"),
        '{"name": "orderservice", "exports": ["dsh.orders.submit"]}\n',
    )

    _write(os.path.join(mods, "mod_c", "src", "__init__.py"), "")
    _write(
        os.path.join(mods, "mod_c", "contract.yaml"),
        "module: mod_c\ndependencies: []\n",
    )
    _write(
        os.path.join(mods, "mod_c", "interface.json"),
        '{"name": "catalog", "exports": []}\n',
    )

    # codegraph db: nodes for the files above, edges for module dependencies
    nodes = [
        ("mod_a/foo/util.py", "util", "modules/mod_a/src/foo/util.py", "mod_a"),
        ("mod_a/foo/__init__.py", "__init__", "modules/mod_a/src/foo/__init__.py", "mod_a"),
        ("mod_b/bar/order.py", "order", "modules/mod_b/src/bar/order.py", "mod_b"),
        ("mod_b/foo/util.py", "util", "modules/mod_b/src/foo/util.py", "mod_b"),
        ("mod_c/__init__.py", "__init__", "modules/mod_c/src/__init__.py", "mod_c"),
    ]
    build_codegraph_db(
        os.path.join(root, "framework-v1", ".codegraph", "codegraph.db"),
        nodes=nodes,
        edges=SAMPLE_EDGES,
    )
    return root


def build_final_task(root):
    """A task that triggers all four conflict kinds.

    mod_a vs mod_b:

    * ``same_name``      — ``dup/thing.py`` placed by mod_a and mod_c.
    * ``naming_conflict``— interface ``"Svc"`` (mod_a) vs ``"svc"`` (mod_b).
    * ``signature_mismatch`` — ``parse`` declared ``(value)`` by mod_a but
      ``(data, **kwargs)`` by mod_b.
    * ``semantic_merge`` — same basename ``util.py`` at different target paths
      (``x/util.py`` vs ``y/util.py``) that both define ``parse``.
    """
    root = os.path.abspath(root)
    mods = os.path.join(root, "modules")

    _write(os.path.join(mods, "mod_a", "src", "x", "util.py"), "def parse(value):\n    return value\n")
    _write(os.path.join(mods, "mod_a", "src", "dup", "thing.py"), "def thing():\n    return 1\n")
    _write(os.path.join(mods, "mod_a", "contract.yaml"), "module: mod_a\ndependencies: []\n")
    _write(os.path.join(mods, "mod_a", "interface.json"), '{"name": "Svc"}\n')

    _write(os.path.join(mods, "mod_b", "src", "y", "util.py"), "def parse(data, **kwargs):\n    return data\n")
    _write(os.path.join(mods, "mod_b", "contract.yaml"), "module: mod_b\ndependencies: [mod_a]\n")
    _write(os.path.join(mods, "mod_b", "interface.json"), '{"name": "svc"}\n')

    _write(os.path.join(mods, "mod_c", "src", "dup", "thing.py"), "def thing():\n    return 2\n")
    _write(os.path.join(mods, "mod_c", "contract.yaml"), "module: mod_c\ndependencies: []\n")
    _write(os.path.join(mods, "mod_c", "interface.json"), '{"name": "catalog"}\n')
    return root


def build_wiring_task(root):
    """A task with a real import to resolve wiring pins (mod_b -> mod_a)."""
    root = os.path.abspath(root)
    mods = os.path.join(root, "modules")

    _write(os.path.join(mods, "mod_a", "src", "util.py"), "def helper():\n    return 1\n")
    _write(
        os.path.join(mods, "mod_a", "contract.yaml"),
        "module: mod_a\ndependencies: []\nread_api:\n  - path: dsh.orders.fetch\n",
    )
    _write(os.path.join(mods, "mod_a", "interface.json"), '{"name": "orders"}\n')

    _write(
        os.path.join(mods, "mod_b", "src", "main.py"),
        "from mod_a import helper\n\ndef go():\n    return helper()\n",
    )
    _write(
        os.path.join(mods, "mod_b", "contract.yaml"),
        "module: mod_b\ndependencies: [mod_a]\nread_api:\n  - path: dsh.orders.submit\n",
    )
    _write(os.path.join(mods, "mod_b", "interface.json"), '{"name": "checkout"}\n')
    return root


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
