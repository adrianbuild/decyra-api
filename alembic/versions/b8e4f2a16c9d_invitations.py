"""invitations table, org-scoped RLS, membership resolver, onboard_user join path

Task 2.3 — Einladungen & Rollen. Lets multiple employees share one
organization: an invited user (matched by email) joins an existing org
instead of founding a new one.

Revision ID: b8e4f2a16c9d
Revises: d3f7a1c95b2e
Create Date: 2026-06-04

"""
from typing import Sequence, Union

from alembic import op


revision: str = "b8e4f2a16c9d"
down_revision: Union[str, Sequence[str], None] = "d3f7a1c95b2e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- invitations table (organization-scoped) -----------------------
    op.execute(
        """
        CREATE TABLE invitations (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL
                REFERENCES organizations(id) ON DELETE CASCADE,
            email text NOT NULL,
            role workspace_role NOT NULL,
            token text NOT NULL UNIQUE,
            invited_by uuid NOT NULL
                REFERENCES users(id) ON DELETE RESTRICT,
            status text NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','accepted','expired','revoked')),
            created_at timestamptz NOT NULL DEFAULT now(),
            expires_at timestamptz NOT NULL
        )
        """
    )
    # NOTE on status: an expired invitation stays 'pending' and is filtered
    # out by `expires_at > now()` at read time. The 'expired' enum value is
    # intentionally unused for now (no cleanup job in 2.3) — kept for a
    # future sweep that flips stale rows. Documented, not an oversight.
    op.create_index(
        "ix_invitations_organization_id", "invitations", ["organization_id"]
    )
    op.create_index("ix_invitations_email", "invitations", ["email"])

    # Org-scoped RLS via the NEW app.current_organization_id GUC (kept
    # separate from app.current_workspace_id: org data <-> org context,
    # workspace data <-> workspace context — future-proof for multi-
    # workspace-per-org).
    op.execute("ALTER TABLE invitations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE invitations FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY invitations_isolation ON invitations
            USING (organization_id =
                current_setting('app.current_organization_id', true)::uuid)
            WITH CHECK (organization_id =
                current_setting('app.current_organization_id', true)::uuid)
        """
    )

    # SELECT/INSERT/UPDATE — no DELETE (invitation history is kept; revoke
    # is a status change, not a delete).
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON invitations TO decyra_app"
    )

    # --- membership resolver (SECURITY DEFINER) ------------------------
    # Resolves user_id -> (org, workspace, role) RLS-bypassed. The
    # invitation endpoints need the caller's own org/role, which is a
    # user-axis query that sharp RLS would filter to nothing (the 2.2c
    # lesson). Returns role too, for require_role.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION current_user_membership(p_user_id uuid)
        RETURNS TABLE (organization_id uuid, workspace_id uuid,
                       role workspace_role)
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            RETURN QUERY
            SELECT w.organization_id, m.workspace_id, m.role
              FROM workspace_members m
              JOIN workspaces w ON w.id = m.workspace_id
             WHERE m.user_id = p_user_id
             ORDER BY w.created_at ASC
             LIMIT 1;
        END;
        $$;
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION current_user_membership(uuid) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION current_user_membership(uuid) "
        "TO decyra_app"
    )

    # --- onboard_user: add the invited path (paths 1 + 3 byte-identical
    #     to 2.2c; the email-bound join is inserted in between) ----------
    op.execute(_ONBOARD_USER_2_3)


def downgrade() -> None:
    # Restore the 2.2c version of onboard_user (no invited path).
    op.execute(_ONBOARD_USER_2_2C)
    op.execute("DROP FUNCTION IF EXISTS current_user_membership(uuid)")
    op.execute("DROP TABLE IF EXISTS invitations")


_ONBOARD_USER_2_3 = """
CREATE OR REPLACE FUNCTION onboard_user(p_user_id uuid, p_email text)
RETURNS TABLE (workspace_id uuid, workspace_name text, created boolean)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_ws_id uuid;
    v_org_id uuid;
    v_local text;
    v_inv invitations%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('onboarding:' || p_user_id::text));

    -- (1) Idempotency — UNCHANGED from 2.2c. NOTE: this fires BEFORE the
    -- invited path, so a user who ALREADY belongs to an org keeps that org
    -- and any invitation is ignored (no multi-org membership in 2.3). This
    -- is an intentional, documented property, not an overlooked gap.
    SELECT m.workspace_id, w.name
      INTO v_ws_id, workspace_name
      FROM workspace_members m
      JOIN workspaces w ON w.id = m.workspace_id
     WHERE m.user_id = p_user_id
     ORDER BY w.created_at ASC
     LIMIT 1;
    IF FOUND THEN
        workspace_id := v_ws_id;
        created := false;
        RETURN NEXT;
        RETURN;
    END IF;

    -- (2) Invited path (2.3) — email-bound, pending, not expired. The
    -- match is on email (not the token): invitations are only as secure as
    -- email verification (see Security-Härtung note re: Confirm-email).
    SELECT * INTO v_inv
      FROM invitations
     WHERE email = p_email
       AND status = 'pending'
       AND expires_at > now()
     ORDER BY created_at DESC
     LIMIT 1;
    IF FOUND THEN
        INSERT INTO users (id, email) VALUES (p_user_id, p_email)
            ON CONFLICT (id) DO NOTHING;
        SELECT id, name INTO v_ws_id, workspace_name
          FROM workspaces
         WHERE organization_id = v_inv.organization_id
         ORDER BY created_at ASC
         LIMIT 1;
        -- No ON CONFLICT needed: path (1) already returned if the user had
        -- ANY membership, and the advisory lock serialises per user, so
        -- here the user has zero memberships — no conflict is possible.
        -- (ON CONFLICT (workspace_id, ...) would also collide with the
        -- workspace_id OUT parameter under variable_conflict=error.)
        INSERT INTO workspace_members (workspace_id, user_id, role)
            VALUES (v_ws_id, p_user_id, v_inv.role);
        UPDATE invitations SET status = 'accepted' WHERE id = v_inv.id;
        workspace_id := v_ws_id;
        created := false;
        RETURN NEXT;
        RETURN;
    END IF;

    -- (3) Founder path — UNCHANGED from 2.2c.
    v_local := split_part(p_email, '@', 1);
    workspace_name := 'Standard-Workspace';
    INSERT INTO users (id, email) VALUES (p_user_id, p_email)
        ON CONFLICT (id) DO NOTHING;
    INSERT INTO organizations (name)
        VALUES (v_local || 's Organisation')
        RETURNING id INTO v_org_id;
    INSERT INTO workspaces (organization_id, name)
        VALUES (v_org_id, workspace_name)
        RETURNING id INTO v_ws_id;
    INSERT INTO workspace_members (workspace_id, user_id, role)
        VALUES (v_ws_id, p_user_id, 'owner');
    workspace_id := v_ws_id;
    created := true;
    RETURN NEXT;
END;
$$;
"""


_ONBOARD_USER_2_2C = """
CREATE OR REPLACE FUNCTION onboard_user(p_user_id uuid, p_email text)
RETURNS TABLE (workspace_id uuid, workspace_name text, created boolean)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_ws_id uuid;
    v_org_id uuid;
    v_local text;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('onboarding:' || p_user_id::text));

    SELECT m.workspace_id, w.name
      INTO v_ws_id, workspace_name
      FROM workspace_members m
      JOIN workspaces w ON w.id = m.workspace_id
     WHERE m.user_id = p_user_id
     ORDER BY w.created_at ASC
     LIMIT 1;
    IF FOUND THEN
        workspace_id := v_ws_id;
        created := false;
        RETURN NEXT;
        RETURN;
    END IF;

    v_local := split_part(p_email, '@', 1);
    workspace_name := 'Standard-Workspace';
    INSERT INTO users (id, email) VALUES (p_user_id, p_email)
        ON CONFLICT (id) DO NOTHING;
    INSERT INTO organizations (name)
        VALUES (v_local || 's Organisation')
        RETURNING id INTO v_org_id;
    INSERT INTO workspaces (organization_id, name)
        VALUES (v_org_id, workspace_name)
        RETURNING id INTO v_ws_id;
    INSERT INTO workspace_members (workspace_id, user_id, role)
        VALUES (v_ws_id, p_user_id, 'owner');
    workspace_id := v_ws_id;
    created := true;
    RETURN NEXT;
END;
$$;
"""
