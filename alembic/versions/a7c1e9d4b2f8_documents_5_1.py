"""documents 5.1: upload/extraction columns + document_events tombstone log

The ``documents`` table already exists (initial schema 313c10e517e1) WITH
workspace RLS (documents_isolation, FORCE) and decyra_app grants
(SELECT/INSERT/UPDATE/DELETE from d3f7a1c95b2e). 5.1 only ADDS columns — they
inherit the existing policy and grants. ``document_events`` is a NEW,
append-only deletion log (separate from the LLM hash-chain); it stores who/when/
which-filename a document was deleted, but NEVER the content (DSGVO: the deleted
text is really gone).

Revision ID: a7c1e9d4b2f8
Revises: f1a2b3c4d5e6
Create Date: 2026-06-18
"""
from alembic import op
import sqlalchemy as sa

revision = "a7c1e9d4b2f8"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None

_NEW_DOC_COLS = (
    "storage_key",
    "mime_type",
    "size_bytes",
    "extracted_text",
    "extraction_status",
)


def upgrade() -> None:
    # --- documents: upload + extraction metadata ------------------------
    # Add with a temporary server_default so the NOT NULL ALTER is safe even
    # against a pre-existing row, then drop the default (new rows must supply
    # real values explicitly).
    op.add_column("documents", sa.Column("storage_key", sa.Text(), nullable=False, server_default=""))
    op.add_column("documents", sa.Column("mime_type", sa.Text(), nullable=False, server_default=""))
    op.add_column("documents", sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column("documents", sa.Column("extracted_text", sa.Text(), nullable=False, server_default=""))
    op.add_column("documents", sa.Column("extraction_status", sa.Text(), nullable=False, server_default="ok"))
    op.create_check_constraint(
        "documents_extraction_status_chk",
        "documents",
        "extraction_status IN ('ok', 'no_text')",
    )
    for col in _NEW_DOC_COLS:
        op.alter_column("documents", col, server_default=None)

    # --- document_events: immutable deletion tombstones -----------------
    op.execute(
        """
        CREATE TABLE document_events (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id uuid NOT NULL
                REFERENCES workspaces(id) ON DELETE CASCADE,
            -- no FK to documents: the row is hard-deleted; we keep its id only
            document_id uuid NOT NULL,
            filename text NOT NULL,
            event_type text NOT NULL CHECK (event_type IN ('deleted')),
            actor_user_id uuid NOT NULL
                REFERENCES users(id) ON DELETE RESTRICT,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.create_index(
        "ix_document_events_workspace",
        "document_events",
        ["workspace_id", "created_at"],
    )
    # Workspace (tenant) RLS, same shape as every other workspace table.
    op.execute("ALTER TABLE document_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE document_events FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY document_events_isolation ON document_events
            USING (workspace_id = current_setting('app.current_workspace_id', true)::uuid)
            WITH CHECK (workspace_id = current_setting('app.current_workspace_id', true)::uuid)
        """
    )
    # Append-only at the grant level: tombstones are immutable (no UPDATE/DELETE).
    op.execute("GRANT SELECT, INSERT ON document_events TO decyra_app")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS document_events_isolation ON document_events")
    op.execute("DROP TABLE IF EXISTS document_events")
    op.drop_constraint("documents_extraction_status_chk", "documents", type_="check")
    for col in reversed(_NEW_DOC_COLS):
        op.drop_column("documents", col)
