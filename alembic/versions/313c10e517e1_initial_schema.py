"""initial schema

Revision ID: 313c10e517e1
Revises:
Create Date: 2026-05-28 22:12:13.540847

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "313c10e517e1"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


RLS_WORKSPACE_SCOPED_TABLES = (
    "workspace_members",
    "audit_events",
    "documents",
    "document_chunks",
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.execute("CREATE TYPE workspace_role AS ENUM ('owner', 'admin', 'user')")

    op.create_table(
        "organizations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("email", sa.Text(), nullable=False, unique=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "workspaces",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "settings",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "workspace_members",
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "role",
            postgresql.ENUM(
                "owner",
                "admin",
                "user",
                name="workspace_role",
                create_type=False,
            ),
            nullable=False,
        ),
    )

    op.create_table(
        "models",
        sa.Column("name", sa.Text(), primary_key=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("cost_input", sa.Numeric(), nullable=False),
        sa.Column("cost_output", sa.Numeric(), nullable=False),
        sa.Column("eu_hosted", sa.Boolean(), nullable=False),
        sa.Column("sovereign_eligible", sa.Boolean(), nullable=False),
        sa.Column("tier_min", sa.Text(), nullable=False),
    )

    op.create_table(
        "audit_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "timestamp",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("request_text", sa.Text(), nullable=False),
        sa.Column("response_text", sa.Text(), nullable=False),
        sa.Column(
            "pii_detected",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("routed_to", sa.Text(), nullable=False),
        sa.Column("prev_hash", sa.Text(), nullable=True),
        sa.Column("current_hash", sa.Text(), nullable=True),
    )
    op.execute(
        "CREATE INDEX ix_audit_events_workspace_id_timestamp "
        "ON audit_events (workspace_id, timestamp DESC)"
    )

    op.create_table(
        "documents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column(
            "uploaded_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_documents_workspace_id", "documents", ["workspace_id"])

    op.create_table(
        "document_chunks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
    )
    # mistral-embed → 1024 dims. Raw DDL because the `vector` type comes from
    # pgvector and isn't a stock SA type; pulling in the `pgvector` Python
    # package just for migrations would be overkill.
    op.execute("ALTER TABLE document_chunks ADD COLUMN embedding vector(1024)")
    op.create_index(
        "ix_document_chunks_workspace_id", "document_chunks", ["workspace_id"]
    )
    op.create_index(
        "ix_document_chunks_document_id", "document_chunks", ["document_id"]
    )

    # Append-only enforcement. REVOKE wouldn't catch the table owner; a
    # trigger blocks everyone, including whatever role ran the migration.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_events_block_modify() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only (% denied)', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_events_no_update
            BEFORE UPDATE ON audit_events
            FOR EACH ROW EXECUTE FUNCTION audit_events_block_modify();
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_events_no_delete
            BEFORE DELETE ON audit_events
            FOR EACH ROW EXECUTE FUNCTION audit_events_block_modify();
        """
    )

    # FORCE = even the table owner obeys the policy. Without it, the
    # dev/test role (postgres = owner) would bypass RLS and the policy
    # would be defense-in-vacuum.
    op.execute("ALTER TABLE workspaces ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE workspaces FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY workspaces_isolation ON workspaces
            USING (id = current_setting('app.current_workspace_id', true)::uuid)
            WITH CHECK (id = current_setting('app.current_workspace_id', true)::uuid)
        """
    )

    for table in RLS_WORKSPACE_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_isolation ON {table}
                USING (workspace_id = current_setting('app.current_workspace_id', true)::uuid)
                WITH CHECK (workspace_id = current_setting('app.current_workspace_id', true)::uuid)
            """
        )


def downgrade() -> None:
    for table in RLS_WORKSPACE_SCOPED_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS workspaces_isolation ON workspaces")
    op.execute("ALTER TABLE workspaces NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE workspaces DISABLE ROW LEVEL SECURITY")

    op.execute("DROP TRIGGER IF EXISTS audit_events_no_delete ON audit_events")
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_update ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS audit_events_block_modify()")

    op.drop_index(
        "ix_document_chunks_document_id", table_name="document_chunks"
    )
    op.drop_index(
        "ix_document_chunks_workspace_id", table_name="document_chunks"
    )
    op.drop_table("document_chunks")
    op.drop_index("ix_documents_workspace_id", table_name="documents")
    op.drop_table("documents")
    op.execute("DROP INDEX IF EXISTS ix_audit_events_workspace_id_timestamp")
    op.drop_table("audit_events")
    op.drop_table("models")
    op.drop_table("workspace_members")
    op.drop_table("workspaces")
    op.drop_table("users")
    op.drop_table("organizations")

    op.execute("DROP TYPE IF EXISTS workspace_role")
    op.execute("DROP EXTENSION IF EXISTS vector")
