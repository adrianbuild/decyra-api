"""chat_attachments table (Task 5B.1): conversation-bound file text storage

Stores the extracted TEXT of files attached to chat messages. Not in the
RAG/documents system — lives fully here, re-injected into LLM context on
every turn. Workspace-scoped RLS (same pattern as document_events).

Revision ID: 0b0a8d270079
Revises: c4d5e6f7a8b9
Create Date: 2026-06-29
"""
from alembic import op

revision = "0b0a8d270079"
down_revision = "c4d5e6f7a8b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE chat_attachments (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id uuid NOT NULL
                REFERENCES workspaces(id) ON DELETE CASCADE,
            conversation_id uuid NOT NULL
                REFERENCES conversations(id) ON DELETE CASCADE,
            filename text NOT NULL,
            mime_type text NOT NULL,
            size_bytes bigint NOT NULL,
            extracted_text text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.create_index(
        "ix_chat_attachments_conversation",
        "chat_attachments",
        ["conversation_id", "created_at"],
    )
    # Workspace (tenant) RLS, same shape as every other workspace table.
    op.execute("ALTER TABLE chat_attachments ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE chat_attachments FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY chat_attachments_isolation ON chat_attachments
            USING (workspace_id = current_setting('app.current_workspace_id', true)::uuid)
            WITH CHECK (workspace_id = current_setting('app.current_workspace_id', true)::uuid)
        """
    )
    # Append-only at the grant level: attachment rows are immutable (no UPDATE/DELETE).
    op.execute("GRANT SELECT, INSERT ON chat_attachments TO decyra_app")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS chat_attachments_isolation ON chat_attachments")
    op.execute("DROP TABLE IF EXISTS chat_attachments")
