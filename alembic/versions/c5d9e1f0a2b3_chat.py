"""conversations + messages tables (chat persistence), workspace-scoped RLS

Task 4.3 — persistent multi-turn conversations behind the chat proxy.
RLS is the workspace (tenant) boundary; the per-user "private" layer is
an explicit user_id filter in the queries (NOT an RLS guarantee), which
is why the privacy test is mandatory.

Revision ID: c5d9e1f0a2b3
Revises: b8e4f2a16c9d
Create Date: 2026-06-04

"""
from typing import Sequence, Union

from alembic import op


revision: str = "c5d9e1f0a2b3"
down_revision: Union[str, Sequence[str], None] = "b8e4f2a16c9d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE conversations (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id uuid NOT NULL
                REFERENCES workspaces(id) ON DELETE CASCADE,
            user_id uuid NOT NULL
                REFERENCES users(id) ON DELETE RESTRICT,
            title text,
            visibility text NOT NULL DEFAULT 'private'
                CHECK (visibility IN ('private')),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    # 'private' is the only allowed value for now (sharing widens this CHECK
    # in a future migration — loud, deliberate). The column exists so the
    # data model is forward-compatible.
    op.create_index(
        "ix_conversations_ws_user",
        "conversations",
        ["workspace_id", "user_id", "updated_at"],
    )

    op.execute(
        """
        CREATE TABLE messages (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            conversation_id uuid NOT NULL
                REFERENCES conversations(id) ON DELETE CASCADE,
            workspace_id uuid NOT NULL
                REFERENCES workspaces(id) ON DELETE CASCADE,
            role text NOT NULL CHECK (role IN ('system','user','assistant')),
            content text NOT NULL,
            model text,
            prompt_tokens integer,
            completion_tokens integer,
            cost numeric,
            -- clock_timestamp() (not now()): messages of one turn are
            -- inserted in a single transaction, so now() would give them
            -- all the same timestamp and ORDER BY created_at would be
            -- non-deterministic (id is a random uuid). Per-statement wall
            -- clock keeps user-before-assistant ordering. Same reason as
            -- the audit_events fix in 3.1.
            created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """
    )
    # workspace_id is denormalised onto messages so the RLS policy keys on
    # it directly (no join), consistent with every other workspace table.
    # The app must always derive it from the parent conversation.
    op.create_index(
        "ix_messages_conversation", "messages", ["conversation_id", "created_at"]
    )

    # RLS = workspace (tenant) boundary on both tables. The per-user
    # "private" layer is enforced by an explicit user_id filter in the
    # queries, NOT here.
    for table in ("conversations", "messages"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_isolation ON {table}
                USING (workspace_id =
                    current_setting('app.current_workspace_id', true)::uuid)
                WITH CHECK (workspace_id =
                    current_setting('app.current_workspace_id', true)::uuid)
            """
        )

    # No DELETE: no chat-deletion feature in 4.3 (loud failing — the
    # feature's migration grants it). Message deletion later rides the
    # ON DELETE CASCADE from conversations (no child DELETE grant needed).
    op.execute("GRANT SELECT, INSERT, UPDATE ON conversations TO decyra_app")
    op.execute("GRANT SELECT, INSERT ON messages TO decyra_app")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS messages")
    op.execute("DROP TABLE IF EXISTS conversations")
