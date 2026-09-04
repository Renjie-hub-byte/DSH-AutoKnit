"""Conflict detection across all four contract conflict kinds.

The merge tool emits a wiring-conflict list covering every kind the framework
contract recognises (``data_contract.shared_enums.merge_conflict_kind``):

* ``same_name``         — two modules place a file at the same target path
                          (detected in :mod:`fw_merge.skeleton`).
* ``naming_conflict``   — two modules name the *same* logical interface
                          differently (case/separator variations of one name).
* ``signature_mismatch`` — the same logical interface is declared with a
                          different signature in different modules.
* ``semantic_merge``    — overlapping logic at different target paths that
                          needs a human to unify (the tool deliberately does
                          **not** do semantic-level fusion, per the task's
                          will_not_have).

Only ``same_name`` / ``naming_conflict`` were detected in the first block;
this final block adds ``signature_mismatch`` and ``semantic_merge`` and folds
all four into :func:`aggregate_conflicts`.
"""

from __future__ import annotations

from typing import List

from .loader import ModuleSource
from .model import Conflict, normalize_interface_name
from .signatures import Signature, extract_signatures
from .skeleton import MergePlan


# ---------------------------------------------------------------------------
# naming_conflict (v1, unchanged)
# ---------------------------------------------------------------------------


def detect_naming_conflicts(modules: List[ModuleSource]) -> List[Conflict]:
    """Find interface names that differ only in spelling (naming conflict).

    Two modules are in conflict when their declared interface names are not
    byte-identical but normalise to the same token (e.g. ``"orderService"`` vs
    ``"orderservice"``).  Importers cannot know which spelling to bind to, so a
    human decision is required.

    Returns:
        Sorted list of ``naming_conflict`` entries (empty when none).
    """
    groups: dict = {}  # normalized name -> {spelling -> [module ids]}
    for mod in modules:
        if not mod.interface_name:
            continue
        norm = normalize_interface_name(mod.interface_name)
        spelling = mod.interface_name
        bucket = groups.setdefault(norm, {})
        bucket.setdefault(spelling, []).append(mod.id)

    conflicts: List[Conflict] = []
    for norm, spellings in sorted(groups.items()):
        distinct = sorted(spellings.keys())
        if len(distinct) <= 1:
            continue
        all_mods = sorted({m for ids in spellings.values() for m in ids})
        conflicts.append(
            Conflict(
                kind="naming_conflict",
                module_refs=all_mods,
                description=(
                    f"同一逻辑接口被不同命名拼写: "
                    + ", ".join(
                        f"'{s}'({', '.join(sorted(spellings[s]))})"
                        for s in distinct
                    )
                    + f" 需人工统一命名后接线, 否则 importers 无法解析接口名。"
                ),
                needs_human=True,
            )
        )
    return conflicts


# ---------------------------------------------------------------------------
# signature_mismatch (final block)
# ---------------------------------------------------------------------------


def _describe_sig(sig: Signature) -> str:
    """``symbol: params`` for a signature in a conflict description."""
    return f"{sig.symbol}{sig.describe()}@{sig.file}:{sig.lineno}"


def detect_signature_mismatches(modules: List[ModuleSource]) -> List[Conflict]:
    """Find interfaces declared with differing signatures across modules.

    Symbols are compared by their normalised name.  When the same logical
    symbol is declared by ≥2 modules with *distinct* signatures, calling code
    cannot bind to a single shape, so the item is flagged ``signature_mismatch``
    and needs a human to unify the signature (or add an adapter).

    BUG-20260904 修复：下划线开头 = 模块私有（Python 惯例），不是跨模块契约，
    不参与比对。此前 script2video 合并时三条误报全是私有符号被卷入
    （``_scene_type`` vs ``SceneType``、``_resolve_root`` vs ``resolve_root``、
    ``_load_manifest`` vs ``load_manifest``），只能人肉放行。

    Returns:
        Sorted list of ``signature_mismatch`` entries (empty when none).
    """
    groups: dict = {}  # norm_symbol -> {sig.key() -> [module ids]}
    samples: dict = {}  # norm_symbol -> {sig.key() -> [Signature]}
    for mod in modules:
        if not mod.has_src():
            continue
        for sym, sig in extract_signatures(mod.src_dir).items():
            if sym.startswith("_"):
                continue  # 模块私有 helper 不构成跨模块接口契约
            norm = normalize_interface_name(sym)
            key = sig.key()
            groups.setdefault(norm, {}).setdefault(key, set()).add(mod.id)
            samples.setdefault(norm, {}).setdefault(key, []).append(sig)

    conflicts: List[Conflict] = []
    for norm, keys in sorted(groups.items()):
        if len(keys) <= 1:
            continue
        all_mods = sorted({m for mods in keys.values() for m in mods})
        detail = "; ".join(
            f"{', '.join(sorted(keys[k]))} -> {_describe_sig(samples[norm][k][0])}"
            for k in sorted(keys, key=lambda x: repr(x))
        )
        conflicts.append(
            Conflict(
                kind="signature_mismatch",
                module_refs=all_mods,
                description=(
                    f"同一逻辑接口 {norm!r} 在多个模块中签名不一致: {detail}; "
                    f"调用方无法绑定单一签名, 需人工统一接口或加适配层。"
                ),
                needs_human=True,
            )
        )
    conflicts.sort(key=lambda c: (c.kind, tuple(c.module_refs), c.description))
    return conflicts


# ---------------------------------------------------------------------------
# semantic_merge (final block)
# ---------------------------------------------------------------------------


def detect_semantic_merges(
    modules: List[ModuleSource], plan: MergePlan
) -> List[Conflict]:
    """Flag overlapping logic that needs manual (semantic) fusion.

    Two files of the same basename that land at *different* target paths and
    both define at least one common symbol are a sign of duplicated / closely
    related logic.  Because the tool deliberately does not merge content
    semantically (will_not_have), each such pair is flagged ``semantic_merge``
    and a human must unify / de-duplicate.

    Files already placed at the *same* target path are excluded here — that is
    a ``same_name`` conflict handled by the skeleton builder.

    Returns:
        Sorted list of ``semantic_merge`` entries (empty when none).
    """
    # basename -> {target_path -> {module: set(symbols)}}
    by_basename: dict = {}
    for mod in modules:
        if not mod.has_src():
            continue
        sigs = extract_signatures(mod.src_dir)
        file_syms: dict = {}
        for sym, sig in sigs.items():
            file_syms.setdefault(sig.file, set()).add(sym)
        for rel in _py_files(mod.src_dir):
            base = rel.rsplit("/", 1)[-1]
            by_basename.setdefault(base, {}).setdefault(rel, {})[mod.id] = set(
                file_syms.get(rel, ())
            )

    conflicts: List[Conflict] = []
    for base, by_path in sorted(by_basename.items()):
        if len(by_path) < 2:
            continue  # same basename only at one target path
        tpaths = sorted(by_path)
        for i in range(len(tpaths)):
            for j in range(i + 1, len(tpaths)):
                p1, p2 = tpaths[i], tpaths[j]
                common = set()
                for m1, s1 in by_path[p1].items():
                    for m2, s2 in by_path[p2].items():
                        common |= s1 & s2
                if not common:
                    continue
                mods = sorted(
                    {m for modmap in (by_path[p1], by_path[p2]) for m in modmap}
                )
                conflicts.append(
                    Conflict(
                        kind="semantic_merge",
                        module_refs=mods,
                        description=(
                            f"同名文件 '{base}' 落在不同目标路径 {p1!r} 与 {p2!r}, "
                            f"共同定义符号 {', '.join(sorted(common))}; "
                            f"自动化合并不做语义级融合, 需人工统一/去重。"
                        ),
                        needs_human=True,
                    )
                )
    conflicts.sort(key=lambda c: (c.kind, tuple(c.module_refs), c.description))
    return conflicts


def _py_files(src_dir: str) -> List[str]:
    """All ``*.py`` paths under ``src_dir`` (relative, posix separators)."""
    import os

    files: List[str] = []
    for dirpath, dirnames, filenames in os.walk(src_dir):
        dirnames.sort()
        filenames.sort()
        for fn in filenames:
            if fn.endswith(".py"):
                files.append(
                    os.path.relpath(os.path.join(dirpath, fn), src_dir).replace(
                        os.sep, "/"
                    )
                )
    return sorted(files)


# ---------------------------------------------------------------------------
# aggregation
# ---------------------------------------------------------------------------


def aggregate_conflicts(
    modules: List[ModuleSource], plan: MergePlan
) -> List[Conflict]:
    """Combine all conflict kinds into one deterministically ordered list.

    Args:
        modules: completed module sources.
        plan: the merge plan (carries ``same_name`` conflicts).

    Returns:
        Combined, sorted conflict list covering all four contract kinds.
    """
    conflicts = (
        list(plan.conflicts)
        + detect_naming_conflicts(modules)
        + detect_signature_mismatches(modules)
        + detect_semantic_merges(modules, plan)
    )
    conflicts.sort(key=lambda c: (c.kind, tuple(c.module_refs), c.description))
    return conflicts


__all__ = [
    "detect_naming_conflicts",
    "detect_signature_mismatches",
    "detect_semantic_merges",
    "aggregate_conflicts",
]
