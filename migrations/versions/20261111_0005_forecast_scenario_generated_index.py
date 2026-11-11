"""index for latest-forecast-per-scenario lookups

The dashboard's default read is ``WHERE scenario = ? ORDER BY generated_timestamp DESC LIMIT 1``.
``idx_forecast_model (model_version, scenario)`` cannot serve it because ``scenario`` is not
the leading column.

Revision ID: 20261111_0005
Revises: 20260509_0004
Create Date: 2026-11-11
"""

from typing import Sequence, Union

from alembic import op

revision: str = "20261111_0005"
down_revision: Union[str, None] = "20260509_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "idx_forecast_scenario_generated",
        "forecast_results",
        ["scenario", "generated_timestamp"],
    )


def downgrade() -> None:
    op.drop_index("idx_forecast_scenario_generated", table_name="forecast_results")
