from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, JSON, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class CaseRecordRow(Base):
    __tablename__ = "case_records"

    case_id: Mapped[UUID] = mapped_column(primary_key=True)
    patient_identifier: Mapped[str] = mapped_column(String(255))
    symptom_onset_date: Mapped[date] = mapped_column(Date)
    hospitalization_date: Mapped[date | None] = mapped_column(Date)
    death_date: Mapped[date | None] = mapped_column(Date)
    location_country: Mapped[str] = mapped_column(String(2), index=True)
    location_airport_code: Mapped[str | None] = mapped_column(String(3))
    confirmed_or_suspected: Mapped[str] = mapped_column(String(32))
    lab_test_result: Mapped[str] = mapped_column(String(64))
    contacts: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    data_source: Mapped[str] = mapped_column(String(100))
    ingestion_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    validation_score: Mapped[float] = mapped_column(Float, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_by: Mapped[str] = mapped_column(String(255), default="system")
    updated_reason: Mapped[str] = mapped_column(String(255), default="New_case")
    observation_kind: Mapped[str] = mapped_column(String(32), default="individual")
    cohort_size: Mapped[int] = mapped_column(Integer, default=1)
    cohort_deaths: Mapped[int] = mapped_column(Integer, default=0)
    report_period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    report_period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    external_observation_key: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CaseRecordHistoryRow(Base):
    __tablename__ = "case_records_history"

    history_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[UUID] = mapped_column(ForeignKey("case_records.case_id"))
    case_data_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    source: Mapped[str] = mapped_column(String(100))
    validation_score: Mapped[float] = mapped_column(Float)
    change_reason: Mapped[str] = mapped_column(String(255))
    changed_by: Mapped[str] = mapped_column(String(255))
    parent_version_id: Mapped[int] = mapped_column(Integer)


class ValidationResultRow(Base):
    __tablename__ = "validation_results"

    validation_id: Mapped[UUID] = mapped_column(primary_key=True)
    case_id: Mapped[UUID] = mapped_column(ForeignKey("case_records.case_id"))
    validation_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    checks_passed: Mapped[dict[str, bool]] = mapped_column(JSON)
    issues: Mapped[list[str]] = mapped_column(JSON)
    quality_score: Mapped[float] = mapped_column(Float)
    quarantine_status: Mapped[str] = mapped_column(String(32))


class InferenceTraceRow(Base):
    __tablename__ = "inference_traces"

    trace_id: Mapped[UUID] = mapped_column(primary_key=True)
    version: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    trigger_type: Mapped[str] = mapped_column(String(32))
    n_cases: Mapped[int] = mapped_column(Integer)
    n_draws: Mapped[int] = mapped_column(Integer, default=2000)
    n_warmup: Mapped[int] = mapped_column(Integer, default=1000)
    posterior_s3_path: Mapped[str] = mapped_column(String(500))
    posterior_summary: Mapped[dict[str, Any]] = mapped_column(JSON)
    diagnostics: Mapped[dict[str, Any]] = mapped_column(JSON)
    execution_time_seconds: Mapped[float] = mapped_column(Float)
    data_quality_mean: Mapped[float] = mapped_column(Float)
    convergence_status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ForecastResultRow(Base):
    __tablename__ = "forecast_results"

    forecast_id: Mapped[UUID] = mapped_column(primary_key=True)
    # Stored for traceability; intentionally not an FK so forecasts remain readable
    # after inference rows are pruned and to avoid insert-order failures across backends.
    model_version: Mapped[str] = mapped_column(String(128), index=True)
    scenario: Mapped[str] = mapped_column(String(100), index=True)
    generated_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    forecast_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    n_simulations: Mapped[int] = mapped_column(Integer, default=10000)
    execution_time_seconds: Mapped[float] = mapped_column(Float)
    s3_archive_path: Mapped[str | None] = mapped_column(String(500))


class UserActionRow(Base):
    __tablename__ = "user_actions"

    action_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    user_id: Mapped[str] = mapped_column(String(255))
    action_type: Mapped[str] = mapped_column(String(100))
    resource_type: Mapped[str] = mapped_column(String(100))
    resource_id: Mapped[str] = mapped_column(String(255))
    details: Mapped[dict[str, Any]] = mapped_column(JSON)
    ip_address: Mapped[str | None] = mapped_column(String(64))


class QualityAlertRow(Base):
    __tablename__ = "quality_alerts"

    alert_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    severity: Mapped[str] = mapped_column(String(32))
    case_id: Mapped[UUID] = mapped_column(ForeignKey("case_records.case_id"))
    issue: Mapped[str] = mapped_column(String(500))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recommended_action: Mapped[str] = mapped_column(String(500))


class ExternalFeedStateRow(Base):
    """Last-known fingerprint for polled external sources (e.g. WHO DON hub + per-item).

    ``feed_key`` may be a composite logical id (e.g. ``{base}:hub``, ``{base}:item:2026-DON123``)
    within ``String(64)``.
    """

    __tablename__ = "external_feed_state"

    feed_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    content_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    last_checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
