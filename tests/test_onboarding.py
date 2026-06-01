"""Task 2.2b — POST /onboarding (workspace provisioning + idempotency)."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

SUB = "11111111-1111-1111-1111-111111111111"
EMAIL = "owner@firma.de"


def _counts(db: Connection) -> dict[str, int]:
    return {
        "orgs": db.execute(
            text("SELECT count(*) FROM organizations")
        ).scalar_one(),
        "workspaces": db.execute(
            text("SELECT count(*) FROM workspaces")
        ).scalar_one(),
        "users": db.execute(text("SELECT count(*) FROM users")).scalar_one(),
        "members": db.execute(
            text("SELECT count(*) FROM workspace_members")
        ).scalar_one(),
    }


@pytest.mark.asyncio
async def test_onboarding_requires_auth(client) -> None:
    r = await client.post("/onboarding")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_onboarding_creates_full_hierarchy(
    client, db: Connection, make_token
) -> None:
    token = make_token(sub=SUB, email=EMAIL)
    r = await client.post(
        "/onboarding", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["created"] is True
    assert body["workspace_name"] == "Standard-Workspace"
    ws_id = body["workspace_id"]

    # Full hierarchy present and wired to the Supabase sub.
    assert _counts(db) == {
        "orgs": 1,
        "workspaces": 1,
        "users": 1,
        "members": 1,
    }
    row = db.execute(
        text(
            "SELECT m.role, m.user_id, u.email "
            "FROM workspace_members m JOIN users u ON u.id = m.user_id "
            "WHERE m.workspace_id = :w"
        ),
        {"w": ws_id},
    ).one()
    assert row.role == "owner"
    assert str(row.user_id) == SUB
    assert row.email == EMAIL


@pytest.mark.asyncio
async def test_onboarding_is_idempotent(
    client, db: Connection, make_token
) -> None:
    token = make_token(sub=SUB, email=EMAIL)
    headers = {"Authorization": f"Bearer {token}"}

    first = await client.post("/onboarding", headers=headers)
    assert first.status_code == 200
    assert first.json()["created"] is True
    ws_id = first.json()["workspace_id"]

    second = await client.post("/onboarding", headers=headers)
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["created"] is False
    assert second_body["workspace_id"] == ws_id

    # No duplicate org/workspace/membership from the second call.
    assert _counts(db) == {
        "orgs": 1,
        "workspaces": 1,
        "users": 1,
        "members": 1,
    }
