"""add models enabled

Revision ID: ebdf5bb9e9da
Revises: 36cbe1faa786
Create Date: 2026-05-31 23:44:19.571710

Task 4.1 — add ``models.enabled`` flag.

Lets us seed placeholder rows (e.g. Google/Vertex AI EU) without
exposing them to the runtime. Defaulting to true keeps any pre-
existing row eligible by default.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "ebdf5bb9e9da"
down_revision: Union[str, Sequence[str], None] = "36cbe1faa786"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "models",
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("models", "enabled")
