"""Shared test helpers — DB seeding and audit-event insertion.

Used by both ``tests/test_hash_chain.py`` and
``tests/test_verify.py`` so the SQL boilerplate lives in one place.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection


def seed_workspace(db: Connection) -> tuple[str, str]:
    """Insert an org, workspace, and user. Return ``(workspace_id, user_id)``."""
    org_id = db.execute(
        text(
            "INSERT INTO organizations (name) VALUES ('Acme') "
            "RETURNING id"
        )
    ).scalar()
    ws_id = str(db.execute(text("SELECT gen_random_uuid()")).scalar())
    db.execute(text(f"SET LOCAL app.current_workspace_id = '{ws_id}'"))
    db.execute(
        text(
            "INSERT INTO workspaces (id, organization_id, name) "
            "VALUES (:i, :o, 'WS')"
        ),
        {"i": ws_id, "o": org_id},
    )
    user_id = str(
        db.execute(
            text(
                "INSERT INTO users (email) VALUES ('a@b.de') "
                "RETURNING id"
            )
        ).scalar()
    )
    return ws_id, user_id


def seed_org_with_owner(
    db: Connection, owner_id: str, owner_email: str = "owner@firma.de"
) -> tuple[str, str]:
    """Org + workspace + an owner user(id=owner_id) + owner membership.
    Returns (org_id, workspace_id)."""
    org_id = db.execute(
        text("INSERT INTO organizations (name) VALUES ('Acme') RETURNING id")
    ).scalar_one()
    ws_id = db.execute(
        text(
            "INSERT INTO workspaces (organization_id, name) "
            "VALUES (:o, 'WS') RETURNING id"
        ),
        {"o": org_id},
    ).scalar_one()
    db.execute(
        text("INSERT INTO users (id, email) VALUES (:i, :e)"),
        {"i": owner_id, "e": owner_email},
    )
    db.execute(
        text(
            "INSERT INTO workspace_members (workspace_id, user_id, role) "
            "VALUES (:w, :u, 'owner')"
        ),
        {"w": ws_id, "u": owner_id},
    )
    return str(org_id), str(ws_id)


def seed_invitation(
    db: Connection,
    org_id: str,
    email: str,
    invited_by: str,
    role: str = "user",
    status: str = "pending",
    expires_days: int = 7,
) -> str:
    """Insert an invitation row. Returns its token."""
    token = f"tok-{email}-{status}"
    db.execute(
        text(
            "INSERT INTO invitations "
            "(organization_id, email, role, token, invited_by, status, "
            " expires_at) "
            "VALUES (:o, :e, :r, :t, :u, :s, "
            "        now() + make_interval(days => :d))"
        ),
        {
            "o": org_id,
            "e": email,
            "r": role,
            "t": token,
            "u": invited_by,
            "s": status,
            "d": expires_days,
        },
    )
    return token


def add_member(
    db: Connection, ws_id: str, user_id: str, role: str = "owner"
) -> None:
    """Add a workspace membership. Assumes the users row already exists
    (e.g. created by ``seed_workspace``)."""
    db.execute(
        text(
            "INSERT INTO workspace_members (workspace_id, user_id, role) "
            "VALUES (:w, :u, :r)"
        ),
        {"w": ws_id, "u": user_id, "r": role},
    )


def insert_event(
    db: Connection,
    ws_id: str,
    user_id: str,
    request: str,
    response: str,
    model: str = "gpt-5",
    *,
    pii_mode: str | None = None,
    anonymized: bool | None = None,
):
    """Insert an audit event through the (v2) hash-chain trigger. pii_mode /
    anonymized are optional; the trigger normalises NULL to 'sovereign'/false."""
    return db.execute(
        text(
            "INSERT INTO audit_events "
            "(workspace_id, user_id, model, request_text, "
            "response_text, routed_to, pii_mode, anonymized) "
            "VALUES (:w, :u, :m, :req, :res, 'openai', :pm, :anon) "
            "RETURNING id, timestamp, prev_hash, current_hash, "
            "canonical_version, pii_mode, anonymized"
        ),
        {
            "w": ws_id,
            "u": user_id,
            "m": model,
            "req": request,
            "res": response,
            "pm": pii_mode,
            "anon": anonymized,
        },
    ).one()


def select_chain(db: Connection, ws_id: str):
    return (
        db.execute(
            text(
                "SELECT id, workspace_id, user_id, timestamp, model, "
                "request_text, response_text, prev_hash, current_hash, "
                "canonical_version, pii_mode, anonymized "
                "FROM audit_events WHERE workspace_id = :w "
                "ORDER BY timestamp ASC, id ASC"
            ),
            {"w": ws_id},
        )
        .mappings()
        .all()
    )
