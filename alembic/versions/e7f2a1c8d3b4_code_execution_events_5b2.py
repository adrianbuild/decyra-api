"""code_execution_events (Task 5B.2): append-only audit of sandbox executions

One row per sandbox run on the code-interpreter path. The governing compliance
decision: the row stores the LLM-GENERATED code (the ``code`` the model returned,
BEFORE de-anonymisation) — NOT the de-anonymised code that actually ran. In
strict mode the generated code references PLACEHOLDER column names
(``[[DCY_PERSON_0]]``), so NO raw column name (PII) ever rests in the audit, the
same way ``audit_events`` holds the anonymised ``request_text``. The de-anon map
is NEVER persisted (it would make placeholders reversible). In sovereign mode
there is no anonymiser, so generated == executed (real columns). The chart is
stored as a SHA-256 HASH (a reference), never the raw PNG bytes — no raw chart at
rest.

Append-only at the GRANT level (like ``document_events`` / ``chat_attachments``):
decyra_app gets SELECT+INSERT only — no UPDATE/DELETE grant. Workspace RLS
(ENABLE + FORCE) with the same policy shape as every other workspace table.

Revision ID: e7f2a1c8d3b4
Revises: 0b0a8d270079
Create Date: 2026-06-30
"""
from alembic import op

revision = "e7f2a1c8d3b4"
down_revision = "0b0a8d270079"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE code_execution_events (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id uuid NOT NULL
                REFERENCES workspaces(id) ON DELETE CASCADE,
            user_id uuid NOT NULL REFERENCES users(id),
            event_type text NOT NULL DEFAULT 'code_execution',
            status text NOT NULL,            -- ok | error | timeout | killed | no_chart
            generated_code text NOT NULL,    -- LLM-GENERATED code (pre de-anon); placeholders in strict
            chart_sha256 text,               -- hex SHA-256 of the chart PNG; NULL when no chart
            created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """
    )
    op.create_index(
        "ix_code_execution_events_workspace",
        "code_execution_events",
        ["workspace_id", "created_at"],
    )
    # Workspace (tenant) RLS, same shape as every other workspace table.
    op.execute("ALTER TABLE code_execution_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE code_execution_events FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY code_execution_events_isolation ON code_execution_events
            USING (workspace_id = current_setting('app.current_workspace_id', true)::uuid)
            WITH CHECK (workspace_id = current_setting('app.current_workspace_id', true)::uuid)
        """
    )
    # Append-only at the grant level: events are immutable (no UPDATE/DELETE).
    op.execute("GRANT SELECT, INSERT ON code_execution_events TO decyra_app")


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS code_execution_events_isolation "
        "ON code_execution_events"
    )
    op.execute("DROP TABLE IF EXISTS code_execution_events")
