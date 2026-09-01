"""AST-based signature extraction for interface-compatibility checks.

We parse each module's Python source with the standard-library :mod:`ast`
module to learn the *public callable shape* of every symbol it declares.  This
is what lets the merge tool detect the two remaining conflict kinds:

* ``signature_mismatch`` — the same logical interface is declared with a
  different signature in different modules;
* ``semantic_merge``     — overlapping logic (files of the same basename at
  different target paths that define the same symbols) that needs a human to
  unify, because the tool deliberately does **not** do semantic fusion.

Nothing here executes module code; only the AST is inspected.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class Signature:
    """The callable shape of one declared symbol.

    Attributes:
        symbol: qualified symbol name (``"Order"``, ``"Order.total"``,
            ``"helper"``).
        file: relative path (posix separators) of the declaring source file.
        lineno: 1-based declaration line (used for deterministic ordering).
        params: positional / keyword-only parameter names.
        n_defaults: number of trailing parameters that carry a default value.
        has_vararg: True when ``*args`` is present.
        has_kwarg: True when ``**kwargs`` is present.
    """

    symbol: str
    file: str
    lineno: int
    params: List[str] = field(default_factory=list)
    n_defaults: int = 0
    has_vararg: bool = False
    has_kwarg: bool = False

    def key(self) -> Tuple:
        """Equality key: two signatures are treated *the same* when these match.

        Parameter names, the number of defaults and the presence of ``*args`` /
        ``**kwargs`` are all part of the wiring contract, so any difference
        makes the interface incompatible at call sites.
        """
        return (
            tuple(self.params),
            self.n_defaults,
            self.has_vararg,
            self.has_kwarg,
        )

    def describe(self) -> str:
        """Human-readable signature, e.g. ``"(self, tax=0.1)"``."""
        parts = []
        if self.has_vararg:
            parts.append("*args")
        parts.extend(self.params)
        if self.has_kwarg:
            parts.append("**kwargs")
        return f"({', '.join(parts)})"


def _signature_of(
    func: ast.FunctionDef, symbol: str, file: str
) -> Signature:
    """Build a :class:`Signature` from a function/async-function definition."""
    args = func.args
    params = (
        [a.arg for a in args.posonlyargs]
        + [a.arg for a in args.args]
        + [a.arg for a in args.kwonlyargs]
    )
    return Signature(
        symbol=symbol,
        file=file,
        lineno=func.lineno,
        params=params,
        n_defaults=len(args.defaults),
        has_vararg=args.vararg is not None,
        has_kwarg=args.kwarg is not None,
    )


def extract_signatures(src_dir: str) -> Dict[str, Signature]:
    """Return the first declared signature for each public symbol under src_dir.

    Symbols are top-level functions / classes plus class methods (named
    ``Class.method``).  Only the *first* declaration (by file then line) is
    kept, so the result is a deterministic ``symbol -> Signature`` map.  Files
    that fail to parse are skipped (they cannot be compiled anyway).
    """
    out: Dict[str, Signature] = {}
    files: List[str] = []
    for dirpath, dirnames, filenames in os.walk(src_dir):
        dirnames.sort()
        filenames.sort()
        for fn in filenames:
            if fn.endswith(".py"):
                files.append(os.path.join(dirpath, fn))

    for full in sorted(files):
        rel = os.path.relpath(full, src_dir).replace(os.sep, "/")
        try:
            with open(full, "r", encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
        except (SyntaxError, OSError):
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.setdefault(node.name, _signature_of(node, node.name, rel))
            elif isinstance(node, ast.ClassDef):
                out.setdefault(node.name, Signature(node.name, rel, node.lineno))
                for body in node.body:
                    if isinstance(body, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        meth = _signature_of(body, f"{node.name}.{body.name}", rel)
                        out.setdefault(meth.symbol, meth)
    return out


__all__ = ["Signature", "extract_signatures"]
