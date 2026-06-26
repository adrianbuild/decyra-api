"""embedding_status 5.2: track per-document embedding lifecycle.

Adds documents.embedding_status (pending|done|failed|skipped). The 5.1 upload
INSERT does NOT supply this column, so we keep a permanent server_default
'pending'; the 5.2 embed step transitions it (done on success, skipped for
no_text, failed on a provider outage). This column is also the seam for a
later async move (no schema change needed then). document_chunks already has
its vector(1024) column + RLS (initial schema) — untouched here.

Revision ID: c4d5e6f7a8b9
Revises: a7c1e9d4b2f8
Create Date: 2026-06-26
"""
from alembic import op
import sqlalchemy as sa

revision = "c4d5e6f7a8b9"
down_revision = "a7c1e9d4b2f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column(
            "embedding_status",
            sa.Text(),
            nullable=False,
            server_default="pending",
        ),
    )
    op.create_check_constraint(
        "documents_embedding_status_chk",
        "documents",
        "embedding_status IN ('pending', 'done', 'failed', 'skipped')",
    )
    # server_default is KEPT on purpose: the 5.1 INSERT omits this column.


def downgrade() -> None:
    op.drop_constraint(
        "documents_embedding_status_chk", "documents", type_="check"
    )
    op.drop_column("documents", "embedding_status")
