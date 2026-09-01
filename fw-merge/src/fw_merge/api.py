"""``dsh.merge.*`` get-handlers.

Implements the two read APIs declared in ``contract.yaml``:

* ``dsh.merge.conflicts`` (get) -> list of conflict items
* ``dsh.merge.skeleton``  (get) -> list of skeleton items

Each handler runs the merge pipeline against a task root and returns the
JSON payload matching the declared ``data_shape``.  Responses use only the
exact field names from the contract (``kind``/``module_refs``/``description``/
``needs_human`` and ``target_path``/``source_module``/``kind``).

The handlers are synchronous, dependency-light wrappers; they can be invoked
both from the CLI (``fw-merge api <name> <task_root>``) and imported by a
future transport layer.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .engine import MergeEngine, MergeResult

#: Supported API names (as they appear in contract.yaml `read_api`).
SUPPORTED_APIS = ("dsh.merge.conflicts", "dsh.merge.skeleton")


def _load(
    task_root: str, db_path: Optional[str] = None, write_interfaces: bool = False
) -> MergeResult:
    return MergeEngine().run(
        task_root=task_root,
        db_path=db_path,
        write_interfaces=write_interfaces,
    )


def get_conflicts(
    task_root: str, db_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """``dsh.merge.conflicts`` (get).

    Returns the wiring-conflict list.  Empty list when there are no conflicts.
    """
    result = _load(task_root, db_path=db_path)
    return result.conflicts_json()


def get_skeleton(
    task_root: str, db_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """``dsh.merge.skeleton`` (get).

    Returns the merged directory-skeleton entries (dirs then files).
    """
    result = _load(task_root, db_path=db_path)
    return result.skeleton_json()


#: Registry used by the CLI and tests to dispatch a get request by API name.
GET_HANDLERS = {
    "dsh.merge.conflicts": get_conflicts,
    "dsh.merge.skeleton": get_skeleton,
}


def get(api_name: str, task_root: str, db_path: Optional[str] = None):
    """Generic dispatcher for ``dsh.merge.*`` get requests.

    Args:
        api_name: one of :data:`SUPPORTED_APIS`.
        task_root: task directory to merge.

    Returns:
        The JSON payload (list of dicts).

    Raises:
        KeyError: if ``api_name`` is not a supported dsh.merge get API.
    """
    if api_name not in GET_HANDLERS:
        raise KeyError(
            f"unsupported dsh.merge get API: {api_name!r}; "
            f"supported: {list(SUPPORTED_APIS)}"
        )
    return GET_HANDLERS[api_name](task_root, db_path=db_path)


__all__ = [
    "SUPPORTED_APIS",
    "GET_HANDLERS",
    "get",
    "get_conflicts",
    "get_skeleton",
]
