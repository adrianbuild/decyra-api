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
):
    return db.execute(
        text(
            "INSERT INTO audit_events "
            "(workspace_id, user_id, model, request_text, "
            "response_text, routed_to) "
            "VALUES (:w, :u, :m, :req, :res, 'openai') "
            "RETURNING id, timestamp, prev_hash, current_hash"
        ),
        {
            "w": ws_id,
            "u": user_id,
            "m": model,
            "req": request,
            "res": response,
        },
    ).one()


def select_chain(db: Connection, ws_id: str):
    return (
        db.execute(
            text(
                "SELECT id, workspace_id, user_id, timestamp, model, "
                "request_text, response_text, prev_hash, current_hash "
                "FROM audit_events WHERE workspace_id = :w "
                "ORDER BY timestamp ASC, id ASC"
            ),
            {"w": ws_id},
        )
        .mappings()
        .all()
    )
