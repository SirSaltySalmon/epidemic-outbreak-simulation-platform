"""case_records cohort / WHO DON upsert columns

Revision ID: 20260508_0003
Revises: 20260508_0002
Create Date: 2026-05-08
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260508_0003"
down_revision: Union[str, None] = "20260508_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "case_records",
        sa.Column("observation_kind", sa.String(length=32), nullable=False, server_default="individual"),
    )
    op.add_column(
        "case_records",
        sa.Column("cohort_size", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "case_records",
        sa.Column("cohort_deaths", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("case_records", sa.Column("report_period_start", sa.Date(), nullable=True))
    op.add_column("case_records", sa.Column("report_period_end", sa.Date(), nullable=True))
    op.add_column("case_records", sa.Column("external_observation_key", sa.String(length=128), nullable=True))
    op.create_unique_constraint("uq_case_records_external_observation_key", "case_records", ["external_observation_key"])


def downgrade() -> None:
    op.drop_constraint("uq_case_records_external_observation_key", "case_records", type_="unique")
    op.drop_column("case_records", "external_observation_key")
    op.drop_column("case_records", "report_period_end")
    op.drop_column("case_records", "report_period_start")
    op.drop_column("case_records", "cohort_deaths")
    op.drop_column("case_records", "cohort_size")
    op.drop_column("case_records", "observation_kind")
