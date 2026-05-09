"""flight snapshot + leg ledger (itinerary ABM design §6.3)

Revision ID: 20260509_0004
Revises: 20260508_0003
Create Date: 2026-05-09
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260509_0004"
down_revision: Union[str, None] = "20260508_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "flight_snapshots",
        sa.Column("snapshot_id", sa.String(length=64), primary_key=True),
        sa.Column("provider_id", sa.String(length=64), nullable=False),
        sa.Column("query_fingerprint", sa.String(length=128), nullable=False),
        sa.Column("fetched_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("horizon_start_date", sa.Date(), nullable=False),
        sa.Column("horizon_end_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
    )
    op.create_table(
        "flight_legs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("snapshot_id", sa.String(length=64), sa.ForeignKey("flight_snapshots.snapshot_id"), nullable=False),
        sa.Column("origin_iata", sa.String(length=8), nullable=False),
        sa.Column("destination_iata", sa.String(length=8), nullable=False),
        sa.Column("scheduled_departure_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scheduled_arrival_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider_record_id", sa.String(length=128), nullable=True),
        sa.Column("equipment_code", sa.String(length=16), nullable=True),
        sa.Column("capacity_ordinal", sa.Integer(), nullable=True),
        sa.Column("leg_status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("supersedes_leg_id", sa.Integer(), sa.ForeignKey("flight_legs.id"), nullable=True),
    )
    op.create_index("ix_flight_legs_snapshot_id", "flight_legs", ["snapshot_id"])


def downgrade() -> None:
    op.drop_index("ix_flight_legs_snapshot_id", table_name="flight_legs")
    op.drop_table("flight_legs")
    op.drop_table("flight_snapshots")
