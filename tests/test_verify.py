"""Task 3.2 — verify endpoints (internal JWT + public token)."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from tests._helpers import add_member, insert_event, seed_workspace


# -- internal endpoint ---------------------------------------------------


@pytest.mark.asyncio
async def test_internal_verify_requires_auth(client, db: Connection) -> None:
    ws_id, _ = seed_workspace(db)
    r = await client.get(f"/workspaces/{ws_id}/audit/verify")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_internal_verify_intact_chain_returns_valid(
    client, db: Connection, make_token
) -> None:
    ws_id, user_id = seed_workspace(db)
    add_member(db, ws_id, user_id)
    for i in range(3):
        insert_event(db, ws_id, user_id, f"req{i}", f"res{i}")
    token = make_token(sub=user_id)
    r = await client.get(
        f"/workspaces/{ws_id}/audit/verify",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is True
    assert body["event_count"] == 3
    assert body["broken_at"] is None


@pytest.mark.pflichttest
@pytest.mark.asyncio
async def test_internal_verify_tampered_row_is_detected(
    client, db: Connection, make_token
) -> None:
    """PFLICHT-TEST (3.2): endpoint reports valid=False with broken_at."""
    ws_id, user_id = seed_workspace(db)
    add_member(db, ws_id, user_id)
    rows = [
        insert_event(db, ws_id, user_id, f"req{i}", f"res{i}")
        for i in range(5)
    ]

    # Tamper as SUPERUSER: disable all triggers (incl. append-only),
    # rewrite one row, restore. Production decyra_app can't do this.
    db.execute(text("SET LOCAL session_replication_role = 'replica'"))
    db.execute(
        text(
            "UPDATE audit_events SET request_text = 'HACKED' "
            "WHERE id = :i"
        ),
        {"i": rows[2].id},
    )
    db.execute(text("SET LOCAL session_replication_role = 'origin'"))

    token = make_token(sub=user_id)
    r = await client.get(
        f"/workspaces/{ws_id}/audit/verify",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is False
    assert body["broken_at"] == 2


@pytest.mark.asyncio
async def test_internal_verify_non_member_returns_403(
    client, db: Connection, make_token
) -> None:
    """A valid JWT for a user who is NOT a member of the workspace is
    rejected with 403, even though the token itself is valid."""
    ws_id, _ = seed_workspace(db)  # no membership for the token's sub
    token = make_token(sub="99999999-9999-9999-9999-999999999999")
    r = await client.get(
        f"/workspaces/{ws_id}/audit/verify",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403


# -- public token endpoint -----------------------------------------------


@pytest.mark.asyncio
async def test_public_verify_with_valid_token_returns_result(
    client, db: Connection, make_verify_token
) -> None:
    ws_id, user_id = seed_workspace(db)
    for i in range(2):
        insert_event(db, ws_id, user_id, f"req{i}", f"res{i}")
    token = make_verify_token(workspace_id=ws_id)
    r = await client.get(f"/v/{token}")
    assert r.status_code == 200
    body = r.json()
    assert body["workspace_id"] == ws_id
    assert body["valid"] is True
    assert body["event_count"] == 2


@pytest.mark.asyncio
async def test_public_verify_with_expired_token_returns_401(
    client, make_verify_token
) -> None:
    token = make_verify_token(
        workspace_id="11111111-1111-1111-1111-111111111111",
        ttl_seconds=-60,
    )
    r = await client.get(f"/v/{token}")
    assert r.status_code == 401
    assert "expired" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_public_verify_with_bad_signature_returns_401(
    client, make_verify_token
) -> None:
    token = make_verify_token(
        workspace_id="11111111-1111-1111-1111-111111111111",
        secret_override="not-the-real-secret-padded-to-32-plus-bytes",
    )
    r = await client.get(f"/v/{token}")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_public_verify_with_non_uuid_sub_returns_401(
    client, make_verify_token
) -> None:
    """Token signed with the real secret but sub is not a UUID."""
    token = make_verify_token(workspace_id="not-a-uuid")
    r = await client.get(f"/v/{token}")
    assert r.status_code == 401
    assert "uuid" in r.json()["detail"].lower()
