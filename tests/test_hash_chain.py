"""Task 3.1 — hash chain mechanics.

Four tests, three on real DB behaviour (genesis NULL, chaining,
workspace independence) and one PFLICHT-TEST that exercises the
Python verify_chain by feeding it a tampered event list.
"""

from __future__ import annotations

import time
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.audit import AuditEventForHash, compute_hash, verify_chain
from tests._helpers import insert_event, seed_workspace, select_chain


def test_first_event_in_workspace_has_null_prev_hash(db: Connection) -> None:
    ws_id, user_id = seed_workspace(db)
    row = insert_event(db, ws_id, user_id, "first", "answer")
    assert row.prev_hash is None
    assert row.current_hash is not None
    assert len(row.current_hash) == 64  # SHA-256 hex


def test_subsequent_events_chain_correctly(db: Connection) -> None:
    ws_id, user_id = seed_workspace(db)
    rows = []
    for i in range(3):
        rows.append(
            insert_event(db, ws_id, user_id, f"req{i}", f"res{i}")
        )
        time.sleep(0.001)  # force distinct microseconds across rows

    # Sanity: each row has a non-zero microsecond component, so the
    # to_char(US) / strftime('%f') canonical mirror is actually exercised.
    assert all(r.timestamp.microsecond != 0 for r in rows), (
        "Timestamps lack microsecond precision — "
        "canonical mirror untested"
    )

    assert rows[0].prev_hash is None
    assert rows[1].prev_hash == rows[0].current_hash
    assert rows[2].prev_hash == rows[1].current_hash

    # Recompute hash[1] from scratch via Python module — must match DB.
    py_hash_1 = compute_hash(
        AuditEventForHash(
            prev_hash=rows[0].current_hash,
            workspace_id=UUID(ws_id),
            user_id=UUID(user_id),
            timestamp=rows[1].timestamp,
            model="gpt-5",
            request_text="req1",
            response_text="res1",
        )
    )
    assert py_hash_1 == rows[1].current_hash


def test_separate_workspaces_have_independent_chains(
    db: Connection,
) -> None:
    ws_a, user_id = seed_workspace(db)

    # Second workspace sharing the same user.
    org_b = db.execute(
        text(
            "INSERT INTO organizations (name) VALUES ('Beta') "
            "RETURNING id"
        )
    ).scalar()
    ws_b = str(db.execute(text("SELECT gen_random_uuid()")).scalar())
    db.execute(text(f"SET LOCAL app.current_workspace_id = '{ws_b}'"))
    db.execute(
        text(
            "INSERT INTO workspaces (id, organization_id, name) "
            "VALUES (:i, :o, 'B')"
        ),
        {"i": ws_b, "o": org_b},
    )

    db.execute(text(f"SET LOCAL app.current_workspace_id = '{ws_a}'"))
    a1 = insert_event(db, ws_a, user_id, "a1", "ra1")
    db.execute(text(f"SET LOCAL app.current_workspace_id = '{ws_b}'"))
    b1 = insert_event(db, ws_b, user_id, "b1", "rb1")
    db.execute(text(f"SET LOCAL app.current_workspace_id = '{ws_a}'"))
    a2 = insert_event(db, ws_a, user_id, "a2", "ra2")
    db.execute(text(f"SET LOCAL app.current_workspace_id = '{ws_b}'"))
    b2 = insert_event(db, ws_b, user_id, "b2", "rb2")

    # Each chain has its own genesis.
    assert a1.prev_hash is None
    assert b1.prev_hash is None
    # Second event in each chain points at the first of the same workspace.
    assert a2.prev_hash == a1.current_hash
    assert b2.prev_hash == b1.current_hash
    # Cross-workspace hashes must not appear in each other's chain.
    assert a2.prev_hash != b1.current_hash
    assert b2.prev_hash != a1.current_hash


@pytest.mark.pflichttest
def test_manipulation_breaks_chain(db: Connection) -> None:
    """PFLICHT-TEST: verify_chain catches a tampered event."""
    ws_id, user_id = seed_workspace(db)
    for i in range(5):
        insert_event(db, ws_id, user_id, f"req{i}", f"res{i}")

    rows = select_chain(db, ws_id)
    intact = [dict(r) for r in rows]

    # Sanity: untouched chain verifies cleanly.
    result = verify_chain(intact)
    assert result.valid is True
    assert result.event_count == 5
    assert result.broken_at is None

    # Tamper event index 2: rewrite request_text.
    tampered = [dict(r) for r in rows]
    tampered[2] = {**tampered[2], "request_text": "HACKED"}

    result = verify_chain(tampered)
    assert result.valid is False
    assert result.broken_at == 2
