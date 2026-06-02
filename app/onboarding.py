"""Task 2.2b/2.2c — first-login workspace provisioning.

The actual provisioning lives in the ``onboard_user()`` SECURITY DEFINER
function (see the 2.2c grants migration). Doing it in the database is what
makes onboarding work under sharp RLS: the idempotency check is a
user-axis query across all workspaces, which workspace-scoped RLS would
otherwise filter to nothing. The function (owner = migration superuser)
bypasses RLS for exactly this vetted logic; the unprivileged app role
only has EXECUTE on it.

This module is the thin Python wrapper that calls the function.
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


def ensure_workspace(
    db: Connection, user_id: str, email: str
) -> OnboardingResult:
    """Return the user's workspace, creating the full hierarchy if absent.

    Idempotent and concurrency-safe (advisory lock inside the function).
    ``user_id`` is the Supabase ``sub`` (UUID), used verbatim as
    ``users.id`` so memberships and audit_events key on the same id.
    """
    row = db.execute(
        text(
            "SELECT workspace_id, workspace_name, created "
            "FROM onboard_user(:u, :e)"
        ),
        {"u": user_id, "e": email},
    ).one()
    return OnboardingResult(
        workspace_id=str(row.workspace_id),
        workspace_name=row.workspace_name,
        created=row.created,
    )
