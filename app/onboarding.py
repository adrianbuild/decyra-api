"""Task 2.2b — first-login workspace provisioning.

When an authenticated user has no workspace yet, ``ensure_workspace``
creates the full tenant hierarchy in one transaction: a local ``users``
mirror row (id = Supabase ``sub``), an organization, a workspace, and an
owner membership. Idempotent: a user who already has a workspace gets
that workspace back, no new rows.

The caller owns the transaction (see ``get_db_write`` in app.main). This
module only issues statements — no begin/commit here.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Connection


@dataclass(frozen=True, slots=True)
class OnboardingResult:
    workspace_id: str
    workspace_name: str
    created: bool


WORKSPACE_NAME = "Standard-Workspace"


def ensure_workspace(
    db: Connection, user_id: str, email: str
) -> OnboardingResult:
    """Return the user's workspace, creating the full hierarchy if absent.

    ``user_id`` is the Supabase ``sub`` (a UUID), used verbatim as the
    local ``users.id`` so memberships and audit_events key on the same id.
    """
    # Serialize concurrent first-time onboarding for the SAME user. Without
    # this, two parallel requests (multi-tab / fast refresh) could both pass
    # the idempotency check below before either writes, creating duplicate
    # orgs. Transaction-scoped, auto-released at commit.
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext('onboarding:' || :u))"),
        {"u": user_id},
    )

    existing = db.execute(
        text(
            "SELECT m.workspace_id, w.name "
            "FROM workspace_members m "
            "JOIN workspaces w ON w.id = m.workspace_id "
            "WHERE m.user_id = :u "
            "ORDER BY w.created_at ASC "
            "LIMIT 1"
        ),
        {"u": user_id},
    ).one_or_none()
    if existing is not None:
        return OnboardingResult(
            workspace_id=str(existing.workspace_id),
            workspace_name=existing.name,
            created=False,
        )

    local_part = email.split("@", 1)[0]
    org_name = f"{local_part}s Organisation"

    # users → org → workspace → membership (FK order).
    db.execute(
        text(
            "INSERT INTO users (id, email) VALUES (:u, :e) "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {"u": user_id, "e": email},
    )
    org_id = db.execute(
        text("INSERT INTO organizations (name) VALUES (:n) RETURNING id"),
        {"n": org_name},
    ).scalar_one()
    ws_id = db.execute(
        text(
            "INSERT INTO workspaces (organization_id, name) "
            "VALUES (:o, :n) RETURNING id"
        ),
        {"o": org_id, "n": WORKSPACE_NAME},
    ).scalar_one()
    db.execute(
        text(
            "INSERT INTO workspace_members (workspace_id, user_id, role) "
            "VALUES (:w, :u, 'owner')"
        ),
        {"w": ws_id, "u": user_id},
    )
    return OnboardingResult(
        workspace_id=str(ws_id),
        workspace_name=WORKSPACE_NAME,
        created=True,
    )
