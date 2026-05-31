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

CANONICAL_VERSION = "v1"


@dataclass(frozen=True, slots=True)
class AuditEventForHash:
    prev_hash: str | None
    workspace_id: UUID
    user_id: UUID
    timestamp: datetime
    model: str
    request_text: str
    response_text: str


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
    return "|".join(
        [
            CANONICAL_VERSION,
            e.prev_hash or "",
            str(e.workspace_id),
            str(e.user_id),
            _format_ts(e.timestamp),
            _length_prefixed(e.model),
            _length_prefixed(e.request_text),
            _length_prefixed(e.response_text),
        ]
    )


def compute_hash(e: AuditEventForHash) -> str:
    return hashlib.sha256(
        canonical_string(e).encode("utf-8")
    ).hexdigest()


def _get(ev: AuditEventForHash | dict, key: str):
    if isinstance(ev, dict):
        return ev[key]
    return getattr(ev, key)


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
