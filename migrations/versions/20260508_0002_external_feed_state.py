"""external feed state for WHO DON polling

Revision ID: 20260508_0002
Revises: 20260507_0001
Create Date: 2026-05-08
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260508_0002"
down_revision: Union[str, None] = "20260507_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "external_feed_state",
        sa.Column("feed_key", sa.String(length=64), primary_key=True),
        sa.Column("content_fingerprint", sa.String(length=128), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_changed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("external_feed_state")
