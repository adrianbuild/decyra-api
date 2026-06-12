"""Audit hash chain — Python mirror of the DB-side trigger logic.

The canonical string is the contract between this module and the
``audit_events_hash_chain()`` trigger in migration
``36cbe1faa786_audit_hash_chain.py``. Any change here must also bump
the version prefix in the trigger function and vice versa.

Format (v1):
    v1|<prev_or_empty>|<ws_uuid>|<user_uuid>|<iso8601_utc_us>|
       <n>:<model>|<n>:<request>|<n>:<response>

where ``n`` is the UTF-8 byte length of the field, matching
``octet_length()`` in plpgsql.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection

@dataclass(frozen=True, slots=True)
class AuditEventForHash:
    prev_hash: str | None
    workspace_id: UUID
    user_id: UUID
    timestamp: datetime
    model: str
    request_text: str
    response_text: str
    # Task 4.5b — per-row canonical version. Existing rows are v1 (the column
    # default backfills them); v2 additionally binds pii_mode + anonymized into
    # the hash. The version literal lives INSIDE the canonical string, so the
    # stored discriminator is self-protecting (faking it flips the formula and
    # the recomputed hash no longer matches).
    canonical_version: str = "v1"
    pii_mode: str | None = None  # only hashed in v2
    anonymized: bool | None = None  # only hashed in v2


@dataclass(frozen=True, slots=True)
class VerifyResult:
    valid: bool
    event_count: int
    broken_at: int | None  # index in the input list, None if intact


def _length_prefixed(s: str) -> str:
    return f"{len(s.encode('utf-8'))}:{s}"


def _format_ts(ts: datetime) -> str:
    # Mirrors to_char(ts AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"').
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def canonical_string(e: AuditEventForHash) -> str:
    parts = [
        e.canonical_version,
        e.prev_hash or "",
        str(e.workspace_id),
        str(e.user_id),
        _format_ts(e.timestamp),
        _length_prefixed(e.model),
        _length_prefixed(e.request_text),
        _length_prefixed(e.response_text),
    ]
    if e.canonical_version == "v2":
        # MUST mirror the plpgsql trigger exactly: length-prefixed pii_mode
        # (normalised to 'sovereign' if absent) + a 'true'/'false' literal.
        parts.append(_length_prefixed(e.pii_mode or "sovereign"))
        parts.append("true" if e.anonymized else "false")
    return "|".join(parts)


def compute_hash(e: AuditEventForHash) -> str:
    return hashlib.sha256(
        canonical_string(e).encode("utf-8")
    ).hexdigest()


def _get(ev: AuditEventForHash | dict, key: str, default=None):
    if isinstance(ev, dict):
        return ev.get(key, default)
    return getattr(ev, key, default)


def verify_chain(
    events: Iterable[AuditEventForHash | dict],
) -> VerifyResult:
    """Recompute the chain and compare to stored ``current_hash``.

    Accepts either ``AuditEventForHash`` instances or dict-like rows
    that additionally carry ``current_hash``. The first event must
    have ``prev_hash=None``; each subsequent event's ``prev_hash``
    must equal the preceding event's ``current_hash``.
    """
    events_list = list(events)
    if not events_list:
        return VerifyResult(valid=True, event_count=0, broken_at=None)

    previous_hash: str | None = None
    for i, ev in enumerate(events_list):
        stored_current = _get(ev, "current_hash")
        stored_prev = _get(ev, "prev_hash")

        if stored_prev != previous_hash:
            return VerifyResult(
                valid=False,
                event_count=len(events_list),
                broken_at=i,
            )

        to_hash = AuditEventForHash(
            prev_hash=stored_prev,
            workspace_id=_get(ev, "workspace_id"),
            user_id=_get(ev, "user_id"),
            timestamp=_get(ev, "timestamp"),
            model=_get(ev, "model"),
            request_text=_get(ev, "request_text"),
            response_text=_get(ev, "response_text"),
            # Default v1 so pre-4.5b rows (and any caller omitting the column)
            # recompute with the original formula.
            canonical_version=_get(ev, "canonical_version", "v1") or "v1",
            pii_mode=_get(ev, "pii_mode"),
            anonymized=_get(ev, "anonymized"),
        )
        if compute_hash(to_hash) != stored_current:
            return VerifyResult(
                valid=False,
                event_count=len(events_list),
                broken_at=i,
            )

        previous_hash = stored_current

    return VerifyResult(
        valid=True, event_count=len(events_list), broken_at=None
    )


def verify_workspace_chain(
    db: Connection, workspace_id: UUID | str
) -> VerifyResult:
    """Read all audit events for a workspace and run verify_chain.

    Order is (timestamp ASC, id ASC), matching insertion order under
    clock_timestamp() so genesis sits at index 0.
    """
    rows = (
        db.execute(
            text(
                "SELECT id, workspace_id, user_id, timestamp, model, "
                "request_text, response_text, prev_hash, current_hash, "
                "canonical_version, pii_mode, anonymized "
                "FROM audit_events WHERE workspace_id = :w "
                "ORDER BY timestamp ASC, id ASC"
            ),
            {"w": str(workspace_id)},
        )
        .mappings()
        .all()
    )
    return verify_chain([dict(r) for r in rows])
