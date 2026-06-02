"""Task 2.2c — proof that RLS actually fires under the unprivileged
decyra_app role. The whole point of 2.2c: without these, the suite could
stay green while RLS silently does nothing (tests as postgres, app as
decyra_app). Every test here asserts authenticity
(current_user='decyra_app', is_superuser='off') so a superuser
fallthrough fails loud."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from tests._helpers import add_member, insert_event, seed_workspace


def _assert_unprivileged(db: Connection) -> None:
    who = db.execute(
        text("SELECT current_user, current_setting('is_superuser')")
    ).one()
    assert who[0] == "decyra_app", f"expected decyra_app, got {who[0]!r}"
    assert who[1] == "off", "RLS proof is worthless if the role is superuser"


def _seed_two_workspaces(db: Connection) -> tuple[str, str, str]:
    """As postgres (RLS bypassed): org, two workspaces A/B, one user, one
    document in each. Returns (ws_a, ws_b, user_id)."""
    org = db.execute(
        text("INSERT INTO organizations (name) VALUES ('Org') RETURNING id")
    ).scalar_one()
    ws_a = db.execute(
        text(
            "INSERT INTO workspaces (organization_id, name) "
            "VALUES (:o, 'A') RETURNING id"
        ),
        {"o": org},
    ).scalar_one()
    ws_b = db.execute(
        text(
            "INSERT INTO workspaces (organization_id, name) "
            "VALUES (:o, 'B') RETURNING id"
        ),
        {"o": org},
    ).scalar_one()
    user = db.execute(
        text("INSERT INTO users (email) VALUES ('u@x.de') RETURNING id")
    ).scalar_one()
    for ws, fn in ((ws_a, "a.pdf"), (ws_b, "b.pdf")):
        db.execute(
            text(
                "INSERT INTO documents (workspace_id, filename, uploaded_by) "
                "VALUES (:w, :fn, :u)"
            ),
            {"w": ws, "fn": fn, "u": user},
        )
    return str(ws_a), str(ws_b), str(user)


def test_rls_blocks_cross_workspace_as_decyra_app(db: Connection) -> None:
    ws_a, ws_b, user = _seed_two_workspaces(db)

    db.execute(text("SET LOCAL ROLE decyra_app"))
    _assert_unprivileged(db)

    # Context = Workspace A.
    db.execute(
        text("SELECT set_config('app.current_workspace_id', :w, true)"),
        {"w": ws_a},
    )

    # (a) A is visible, B is NOT. THIS is the proof RLS fires — if B's
    # row showed up here, RLS would be off (the silent failure).
    visible = (
        db.execute(text("SELECT filename FROM documents ORDER BY filename"))
        .scalars()
        .all()
    )
    assert visible == ["a.pdf"]

    # Cross-workspace INSERT is rejected by the WITH CHECK policy
    # (savepoint so the aborted statement doesn't poison teardown).
    with pytest.raises(Exception) as exc:
        with db.begin_nested():
            db.execute(
                text(
                    "INSERT INTO documents (workspace_id, filename, "
                    "uploaded_by) VALUES (:b, 'evil.pdf', :u)"
                ),
                {"b": ws_b, "u": user},
            )
    assert "row-level security" in str(exc.value).lower()


def test_rls_audit_events_append_only_grant(db: Connection) -> None:
    """decyra_app has SELECT+INSERT on audit_events but no UPDATE — the
    append-only guarantee at the role level, on top of the trigger."""
    ws_id, user_id = seed_workspace(db)
    add_member(db, ws_id, user_id)
    insert_event(db, ws_id, user_id, "req", "res")

    db.execute(text("SET LOCAL ROLE decyra_app"))
    _assert_unprivileged(db)

    with pytest.raises(Exception) as exc:
        with db.begin_nested():
            db.execute(text("UPDATE audit_events SET request_text = 'x'"))
    assert "permission denied" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_rls_endpoint_isolation_as_decyra_app(
    client_decyra_app, make_token
) -> None:
    """Full request path as decyra_app: onboard (SECURITY DEFINER), verify
    own workspace (200), verify a foreign workspace (403 — membership is
    RLS-scoped, no leak)."""
    token = make_token(
        sub="22222222-2222-2222-2222-222222222222", email="m@x.de"
    )
    headers = {"Authorization": f"Bearer {token}"}

    r = await client_decyra_app.post("/onboarding", headers=headers)
    assert r.status_code == 200
    ws_id = r.json()["workspace_id"]

    own = await client_decyra_app.get(
        f"/workspaces/{ws_id}/audit/verify", headers=headers
    )
    assert own.status_code == 200

    foreign = "33333333-3333-3333-3333-333333333333"
    other = await client_decyra_app.get(
        f"/workspaces/{foreign}/audit/verify", headers=headers
    )
    assert other.status_code == 403
