"""Local document storage (Task 5.1).

The storage KEY is built only from UUIDs ({workspace_id}/{document_id}{ext}) —
never from the user-supplied filename — so there is no path-traversal vector and
no collision. The DB row (RLS-protected) is the access gatekeeper; the
workspace_id prefix in the key is defense-in-depth, not the primary boundary.
Object storage can replace this module later without touching the endpoints.
"""

import os
from pathlib import Path


def build_storage_key(workspace_id: str, document_id: str, ext: str) -> str:
    """Internal key from UUIDs only. ``ext`` includes the leading dot."""
    return f"{workspace_id}/{document_id}{ext}"


def _resolve_within(base_dir: str, key: str) -> Path:
    """Resolve base_dir/key and REFUSE anything that escapes base_dir. The key
    is UUID-derived, but this guard makes traversal structurally impossible."""
    base = Path(base_dir).resolve()
    target = (base / key).resolve()
    if target != base and base not in target.parents:
        raise ValueError("storage path escapes base directory")
    return target


def write_document(base_dir: str, key: str, data: bytes) -> None:
    """Write atomically: temp file + os.replace, so a crash never leaves a
    half-written document at the final path."""
    target = _resolve_within(base_dir, key)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, target)


def delete_document(base_dir: str, key: str) -> None:
    """Idempotent unlink (missing is fine — delete is best-effort after the DB
    row, the source of truth, is already gone)."""
    target = _resolve_within(base_dir, key)
    target.unlink(missing_ok=True)


def document_path(base_dir: str, key: str) -> Path:
    """Resolved absolute path for a key (used by 5.2+ to re-read the raw file)."""
    return _resolve_within(base_dir, key)
