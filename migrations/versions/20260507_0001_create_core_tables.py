"""create core tables

Revision ID: 20260507_0001
Revises:
Create Date: 2026-05-07 00:01:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260507_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "case_records",
        sa.Column("case_id", sa.Uuid(), primary_key=True),
        sa.Column("patient_identifier", sa.String(length=255), nullable=False),
        sa.Column("symptom_onset_date", sa.Date(), nullable=False),
        sa.Column("hospitalization_date", sa.Date(), nullable=True),
        sa.Column("death_date", sa.Date(), nullable=True),
        sa.Column("location_country", sa.String(length=2), nullable=False),
        sa.Column("location_airport_code", sa.String(length=3), nullable=True),
        sa.Column("confirmed_or_suspected", sa.String(length=32), nullable=False),
        sa.Column("lab_test_result", sa.String(length=64), nullable=False),
        sa.Column("contacts", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("data_source", sa.String(length=100), nullable=False),
        sa.Column("ingestion_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("validation_score", sa.Float(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_by", sa.String(length=255), nullable=False, server_default="system"),
        sa.Column("updated_reason", sa.String(length=255), nullable=False, server_default="New_case"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_case_symptom_date", "case_records", ["symptom_onset_date"])
    op.create_index("idx_case_country", "case_records", ["location_country"])
    op.create_index("idx_case_validation", "case_records", ["validation_score"])

    op.create_table(
        "case_records_history",
        sa.Column("history_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("case_id", sa.Uuid(), sa.ForeignKey("case_records.case_id"), nullable=False),
        sa.Column("case_data_json", sa.JSON(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("validation_score", sa.Float(), nullable=False),
        sa.Column("change_reason", sa.String(length=255), nullable=False),
        sa.Column("changed_by", sa.String(length=255), nullable=False),
        sa.Column("parent_version_id", sa.Integer(), nullable=False),
    )

    op.create_table(
        "validation_results",
        sa.Column("validation_id", sa.Uuid(), primary_key=True),
        sa.Column("case_id", sa.Uuid(), sa.ForeignKey("case_records.case_id"), nullable=False),
        sa.Column("validation_timestamp", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("checks_passed", sa.JSON(), nullable=False),
        sa.Column("issues", sa.JSON(), nullable=False),
        sa.Column("quality_score", sa.Float(), nullable=False),
        sa.Column("quarantine_status", sa.String(length=32), nullable=False),
    )

    op.create_table(
        "inference_traces",
        sa.Column("trace_id", sa.Uuid(), primary_key=True),
        sa.Column("version", sa.String(length=50), nullable=False, unique=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trigger_type", sa.String(length=32), nullable=False),
        sa.Column("n_cases", sa.Integer(), nullable=False),
        sa.Column("n_draws", sa.Integer(), nullable=False, server_default="2000"),
        sa.Column("n_warmup", sa.Integer(), nullable=False, server_default="1000"),
        sa.Column("posterior_s3_path", sa.String(length=500), nullable=False),
        sa.Column("posterior_summary", sa.JSON(), nullable=False),
        sa.Column("diagnostics", sa.JSON(), nullable=False),
        sa.Column("execution_time_seconds", sa.Float(), nullable=False),
        sa.Column("data_quality_mean", sa.Float(), nullable=False),
        sa.Column("convergence_status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_trace_version", "inference_traces", ["version"])

    op.create_table(
        "forecast_results",
        sa.Column("forecast_id", sa.Uuid(), primary_key=True),
        sa.Column("model_version", sa.String(length=50), sa.ForeignKey("inference_traces.version"), nullable=False),
        sa.Column("scenario", sa.String(length=100), nullable=False),
        sa.Column("generated_timestamp", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("forecast_json", sa.JSON(), nullable=False),
        sa.Column("n_simulations", sa.Integer(), nullable=False, server_default="10000"),
        sa.Column("execution_time_seconds", sa.Float(), nullable=False),
        sa.Column("s3_archive_path", sa.String(length=500), nullable=True),
    )
    op.create_index("idx_forecast_model", "forecast_results", ["model_version", "scenario"])

    op.create_table(
        "user_actions",
        sa.Column("action_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("action_type", sa.String(length=100), nullable=False),
        sa.Column("resource_type", sa.String(length=100), nullable=False),
        sa.Column("resource_id", sa.String(length=255), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
    )

    op.create_table(
        "quality_alerts",
        sa.Column("alert_id", sa.String(length=64), primary_key=True),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("case_id", sa.Uuid(), sa.ForeignKey("case_records.case_id"), nullable=False),
        sa.Column("issue", sa.String(length=500), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recommended_action", sa.String(length=500), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("quality_alerts")
    op.drop_table("user_actions")
    op.drop_index("idx_forecast_model", table_name="forecast_results")
    op.drop_table("forecast_results")
    op.drop_index("idx_trace_version", table_name="inference_traces")
    op.drop_table("inference_traces")
    op.drop_table("validation_results")
    op.drop_table("case_records_history")
    op.drop_index("idx_case_validation", table_name="case_records")
    op.drop_index("idx_case_country", table_name="case_records")
    op.drop_index("idx_case_symptom_date", table_name="case_records")
    op.drop_table("case_records")
