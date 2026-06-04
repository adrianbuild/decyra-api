"""Task 2.3 — invitation logic: membership resolution, role enforcement,
and invitation CRUD helpers.

The endpoints (app/main.py) follow the same shape every time:
    m = resolve_membership(db, user_id)   # SECURITY DEFINER, RLS-bypassed
    require_role(m, {"owner", "admin"})   # 403 otherwise
    set_org_context(db, m.organization_id)
    ... org-scoped invitation query under RLS ...
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.engine import Connection

# Roles allowed to manage invitations, and roles an invite may grant.
MANAGER_ROLES = {"owner", "admin"}
INVITABLE_ROLES = {"admin", "user"}  # owner is the founder; no owner-invite


@dataclass(frozen=True, slots=True)
class Membership:
    organization_id: str
    workspace_id: str
    role: str


def resolve_membership(db: Connection, user_id: str) -> Membership | None:
    """user_id -> (org, workspace, role) via the SECURITY DEFINER resolver
    (RLS-bypassed; a user-axis lookup can't run under sharp RLS). Returns
    None if the user has no membership yet."""
    row = db.execute(
        text(
            "SELECT organization_id, workspace_id, role "
            "FROM current_user_membership(:u)"
        ),
        {"u": user_id},
    ).one_or_none()
    if row is None:
        return None
    return Membership(
        organization_id=str(row.organization_id),
        workspace_id=str(row.workspace_id),
        role=row.role,
    )


def require_role(member: Membership | None, allowed: set[str]) -> None:
    if member is None or member.role not in allowed:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="insufficient role for this action",
        )


def create_invitation(
    db: Connection, org_id: str, email: str, role: str, invited_by: str
) -> dict:
    """Insert a pending invitation (7-day TTL). Caller must have set the
    org context and validated the role first."""
    token = secrets.token_urlsafe(32)  # unguessable
    row = db.execute(
        text(
            "INSERT INTO invitations "
            "(organization_id, email, role, token, invited_by, expires_at) "
            "VALUES (:o, :e, :r, :t, :u, now() + interval '7 days') "
            "RETURNING id, email, role, token, status, expires_at"
        ),
        {"o": org_id, "e": email, "r": role, "t": token, "u": invited_by},
    ).one()
    return {
        "id": str(row.id),
        "email": row.email,
        "role": row.role,
        "token": row.token,
        "status": row.status,
        "expires_at": row.expires_at.isoformat(),
    }


def list_pending_invitations(db: Connection, org_id: str) -> list[dict]:
    """Pending invitations of the org. Explicit org filter for defense in
    depth — RLS (org context) is the second layer, not the only one."""
    rows = db.execute(
        text(
            "SELECT id, email, role, token, status, created_at, expires_at "
            "FROM invitations "
            "WHERE status = 'pending' AND organization_id = :o "
            "ORDER BY created_at DESC"
        ),
        {"o": org_id},
    ).all()
    return [
        {
            "id": str(r.id),
            "email": r.email,
            "role": r.role,
            "token": r.token,
            "status": r.status,
            "created_at": r.created_at.isoformat(),
            "expires_at": r.expires_at.isoformat(),
        }
        for r in rows
    ]


def revoke_invitation(db: Connection, token: str, org_id: str) -> bool:
    """Mark a pending invitation revoked. Explicit org filter (defense in
    depth, RLS second layer) so a foreign-org token yields no row -> False
    -> 404, no cross-org revoke."""
    row = db.execute(
        text(
            "UPDATE invitations SET status = 'revoked' "
            "WHERE token = :t AND status = 'pending' "
            "AND organization_id = :o RETURNING id"
        ),
        {"t": token, "o": org_id},
    ).first()
    return row is not None
