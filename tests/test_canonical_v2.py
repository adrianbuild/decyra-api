"""Task 4.5b — per-row canonical version (v1/v2) in the audit hash chain.

v2 adds ``pii_mode`` + ``anonymized`` to the hashed canonical string, so the
chain cryptographically attests which mode governed each request and whether
it was anonymised. Existing production rows stay v1 (column default backfill);
each row carries its own version and ``verify_chain`` recomputes per-row, so a
MIXED v1/v2 chain must still verify across the version boundary.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.audit import (
    AuditEventForHash,
    compute_hash,
    verify_chain,
    verify_workspace_chain,
)
from tests._helpers import insert_event, seed_workspace, select_chain

WS = UUID("11111111-1111-1111-1111-111111111111")
USER = UUID("22222222-2222-2222-2222-222222222222")
TS = datetime(2026, 6, 12, 10, 0, 0, 123456, tzinfo=timezone.utc)


def _v1(prev, req="r", res="a") -> AuditEventForHash:
    return AuditEventForHash(
        prev_hash=prev, workspace_id=WS, user_id=USER, timestamp=TS,
        model="gpt-5", request_text=req, response_text=res,
    )


def _v2(prev, *, pii_mode="strict", anonymized=True, req="r", res="a") -> AuditEventForHash:
    return AuditEventForHash(
        prev_hash=prev, workspace_id=WS, user_id=USER, timestamp=TS,
        model="gpt-5", request_text=req, response_text=res,
        canonical_version="v2", pii_mode=pii_mode, anonymized=anonymized,
    )


def _row(e: AuditEventForHash) -> dict:
    return {
        "prev_hash": e.prev_hash,
        "current_hash": compute_hash(e),
        "workspace_id": e.workspace_id,
        "user_id": e.user_id,
        "timestamp": e.timestamp,
        "model": e.model,
        "request_text": e.request_text,
        "response_text": e.response_text,
        "canonical_version": e.canonical_version,
        "pii_mode": e.pii_mode,
        "anonymized": e.anonymized,
    }


def test_v1_hash_is_unaffected_by_the_new_fields() -> None:
    """A v1 event hashes exactly as before — old chains cannot break."""
    bare = compute_hash(_v1(None))
    # Setting v2-only fields on a v1 event must not change its hash.
    with_fields = compute_hash(
        AuditEventForHash(
            prev_hash=None, workspace_id=WS, user_id=USER, timestamp=TS,
            model="gpt-5", request_text="r", response_text="a",
            canonical_version="v1", pii_mode="strict", anonymized=True,
        )
    )
    assert bare == with_fields


def test_pii_mode_and_anonymized_are_part_of_the_v2_hash() -> None:
    base = compute_hash(_v2(None, pii_mode="strict", anonymized=True))
    assert base != compute_hash(_v2(None, pii_mode="sovereign", anonymized=True))
    assert base != compute_hash(_v2(None, pii_mode="strict", anonymized=False))


def test_mixed_v1_v2_chain_verifies() -> None:
    e0 = _v1(None, req="r0")
    r0 = _row(e0)
    e1 = _v1(r0["current_hash"], req="r1")
    r1 = _row(e1)
    e2 = _v2(r1["current_hash"], pii_mode="strict", anonymized=True, req="r2")
    r2 = _row(e2)
    e3 = _v2(r2["current_hash"], pii_mode="sovereign", anonymized=False, req="r3")
    r3 = _row(e3)

    result = verify_chain([r0, r1, r2, r3])
    assert result.valid is True
    assert result.event_count == 4
    assert result.broken_at is None


def test_tampering_a_v2_mode_breaks_the_mixed_chain() -> None:
    e0 = _v1(None, req="r0")
    r0 = _row(e0)
    e1 = _v2(r0["current_hash"], pii_mode="strict", anonymized=True, req="r1")
    r1 = _row(e1)
    # Flip the recorded mode without recomputing the hash → must be caught.
    tampered = {**r1, "pii_mode": "sovereign"}
    result = verify_chain([r0, tampered])
    assert result.valid is False
    assert result.broken_at == 1


# --- DB-level: the trigger emits v2 and matches the Python mirror -------


def test_db_insert_emits_v2_and_python_mirror_matches(db: Connection) -> None:
    ws_id, user_id = seed_workspace(db)
    row = insert_event(
        db, ws_id, user_id, "frage", "antwort",
        pii_mode="strict", anonymized=True,
    )
    assert row.canonical_version == "v2"
    assert row.pii_mode == "strict"
    assert row.anonymized is True

    py_hash = compute_hash(
        AuditEventForHash(
            prev_hash=row.prev_hash,
            workspace_id=UUID(ws_id),
            user_id=UUID(user_id),
            timestamp=row.timestamp,
            model="gpt-5",
            request_text="frage",
            response_text="antwort",
            canonical_version="v2",
            pii_mode="strict",
            anonymized=True,
        )
    )
    assert py_hash == row.current_hash


def test_db_insert_without_mode_normalises_to_sovereign_false(
    db: Connection,
) -> None:
    ws_id, user_id = seed_workspace(db)
    row = insert_event(db, ws_id, user_id, "q", "a")  # no pii_mode/anonymized
    assert row.canonical_version == "v2"
    assert row.pii_mode == "sovereign"
    assert row.anonymized is False


def test_db_mixed_v1_and_v2_chain_verifies(db: Connection) -> None:
    """Production realism: a workspace whose early rows are v1 (written by the
    old trigger) and whose later rows are v2 (the 4.5b trigger) must verify
    across the version boundary. We seed the v1 rows with the trigger disabled
    (computing their v1 hashes in Python), then append real v2 rows."""
    ws_id, user_id = seed_workspace(db)

    db.execute(
        text(
            "ALTER TABLE audit_events DISABLE TRIGGER "
            "audit_events_hash_chain_insert"
        )
    )
    prev = None
    for i in range(2):
        ts = datetime(2020, 1, 1, 12, 0, i, 500000, tzinfo=timezone.utc)
        h = compute_hash(
            AuditEventForHash(
                prev_hash=prev,
                workspace_id=UUID(ws_id),
                user_id=UUID(user_id),
                timestamp=ts,
                model="gpt-5",
                request_text=f"old{i}",
                response_text=f"oldres{i}",
                canonical_version="v1",
            )
        )
        db.execute(
            text(
                "INSERT INTO audit_events (workspace_id, user_id, timestamp, "
                "model, request_text, response_text, routed_to, prev_hash, "
                "current_hash, canonical_version) "
                "VALUES (:w, :u, :ts, 'gpt-5', :req, :res, 'openai', :prev, "
                ":cur, 'v1')"
            ),
            {
                "w": ws_id, "u": user_id, "ts": ts,
                "req": f"old{i}", "res": f"oldres{i}",
                "prev": prev, "cur": h,
            },
        )
        prev = h
    db.execute(
        text(
            "ALTER TABLE audit_events ENABLE TRIGGER "
            "audit_events_hash_chain_insert"
        )
    )

    # Append real v2 rows through the live trigger.
    insert_event(db, ws_id, user_id, "new0", "newres0", pii_mode="strict", anonymized=True)
    insert_event(db, ws_id, user_id, "new1", "newres1", pii_mode="sovereign", anonymized=False)

    rows = select_chain(db, ws_id)
    versions = [r["canonical_version"] for r in rows]
    assert versions == ["v1", "v1", "v2", "v2"]  # boundary present
    # The first v2 row chains onto the last v1 row's hash.
    assert rows[2]["prev_hash"] == rows[1]["current_hash"]

    result = verify_workspace_chain(db, ws_id)
    assert result.valid is True
    assert result.event_count == 4
    assert result.broken_at is None

