"""decyra_app role hardening, onboarding function, and grants

Task 2.2c — switch the app to the unprivileged decyra_app role so RLS
actually fires at runtime. This migration is the single source of truth
for the role's attributes and privileges (conftest no longer grants).

Runs as the migration superuser (MIGRATION_DATABASE_URL = postgres).
decyra_app itself cannot run DDL or GRANT.

Revision ID: d3f7a1c95b2e
Revises: ebdf5bb9e9da
Create Date: 2026-06-02

"""
from typing import Sequence, Union

from alembic import op


revision: str = "d3f7a1c95b2e"
down_revision: Union[str, Sequence[str], None] = "ebdf5bb9e9da"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (table, grant list). audit_events is SELECT+INSERT only — append-only
# enforced at the role level (no UPDATE/DELETE), on top of the trigger.
_GRANTS = {
    "audit_events": "SELECT, INSERT",
    "workspaces": "SELECT, INSERT, UPDATE, DELETE",
    "workspace_members": "SELECT, INSERT, UPDATE, DELETE",
    "documents": "SELECT, INSERT, UPDATE, DELETE",
    "document_chunks": "SELECT, INSERT, UPDATE, DELETE",
    "organizations": "SELECT, INSERT, UPDATE",
    "users": "SELECT, INSERT, UPDATE",
    "models": "SELECT",
}


def upgrade() -> None:
    # Role: ensure it exists with the right attributes. LOGIN is not a
    # secret and belongs here; the PASSWORD is set out-of-band (.env /
    # docker init for dev, infra for prod) and never committed.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'decyra_app') THEN
                CREATE ROLE decyra_app LOGIN NOSUPERUSER NOBYPASSRLS;
            END IF;
        END
        $$;
        """
    )
    # ALTER is idempotent and fixes a pre-existing NOLOGIN role (the dev
    # snapshot had rolcanlogin=f).
    op.execute("ALTER ROLE decyra_app LOGIN NOSUPERUSER NOBYPASSRLS")

    op.execute("GRANT USAGE ON SCHEMA public TO decyra_app")
    for table, grants in _GRANTS.items():
        op.execute(f"GRANT {grants} ON {table} TO decyra_app")

    # Onboarding under sharp RLS is a hen-and-egg problem: the idempotency
    # check is a user-axis query across ALL workspaces, which workspace-
    # scoped RLS would filter to nothing (-> duplicate orgs). This runs the
    # whole provisioning as SECURITY DEFINER (owner = migration superuser,
    # bypasses RLS), confined to exactly this vetted logic. Fixed
    # search_path = pg_catalog, public closes the SECURITY DEFINER
    # escalation hole (same hardening as the hash-chain trigger in 3.1).
    op.execute(
        """
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
            PERFORM pg_advisory_xact_lock(
                hashtext('onboarding:' || p_user_id::text)
            );

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
    )
    op.execute(
        "REVOKE ALL ON FUNCTION onboard_user(uuid, text) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION onboard_user(uuid, text) TO decyra_app"
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS onboard_user(uuid, text)")
    op.execute("REVOKE USAGE ON SCHEMA public FROM decyra_app")
    for table in _GRANTS:
        op.execute(f"REVOKE ALL ON {table} FROM decyra_app")
    # Keep the role (cluster-level, other DBs may depend); just lock it out.
    op.execute("ALTER ROLE decyra_app NOLOGIN")
