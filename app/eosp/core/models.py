from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator


class CaseStatus(StrEnum):
    CONFIRMED = "confirmed"
    SUSPECTED = "suspected"


class ObservationKind(StrEnum):
    INDIVIDUAL = "individual"
    COHORT = "cohort"


class LabResult(StrEnum):
    PCR_POSITIVE = "PCR_positive"
    PCR_NEGATIVE = "PCR_negative"
    SEROLOGY_POSITIVE = "serology_positive"
    NOT_TESTED = "not_tested"


class TriggerType(StrEnum):
    NEW_CASE = "new_case"
    CRON = "cron"
    ANOMALY = "anomaly"
    MANUAL = "manual"
    EXTERNAL_FEED = "external_feed"


class ConvergenceStatus(StrEnum):
    CONVERGED = "CONVERGED"
    WARNING = "WARNING"
    FAILED = "FAILED"


class CaseRecord(BaseModel):
    case_id: UUID
    patient_identifier: str
    symptom_onset_date: date
    hospitalization_date: date | None = None
    death_date: date | None = None
    location_country: str = Field(min_length=2, max_length=2)
    location_airport_code: str | None = Field(default=None, min_length=3, max_length=3)
    confirmed_or_suspected: CaseStatus
    lab_test_result: LabResult
    contacts: list[dict[str, Any]] = Field(default_factory=list)
    data_source: str
    ingestion_timestamp: datetime
    validation_score: float = Field(ge=0, le=1)
    version: int = 1
    updated_by: str = "system"
    updated_reason: str = "New_case"
    observation_kind: ObservationKind = ObservationKind.INDIVIDUAL
    cohort_size: int = Field(default=1, ge=1)
    cohort_deaths: int = Field(default=0, ge=0)
    report_period_start: date | None = None
    report_period_end: date | None = None
    external_observation_key: str | None = Field(default=None, max_length=128)
    #: Last time this row was created or updated in EOSP (dashboard “Updated …” freshness).
    record_updated_at: datetime


class CaseCreate(BaseModel):
    case_id: UUID = Field(default_factory=uuid4)
    patient_identifier: str
    symptom_onset_date: date
    hospitalization_date: date | None = None
    death_date: date | None = None
    location_country: str = Field(min_length=2, max_length=2)
    location_airport_code: str | None = Field(default=None, min_length=3, max_length=3)
    confirmed_or_suspected: CaseStatus
    lab_test_result: LabResult
    contacts: list[dict[str, Any]] = Field(default_factory=list)
    data_source: str
    ingestion_timestamp: datetime | None = None
    updated_by: str = "api"
    updated_reason: str = "New_case"
    observation_kind: ObservationKind = ObservationKind.INDIVIDUAL
    cohort_size: int = Field(default=1, ge=1)
    cohort_deaths: int = Field(default=0, ge=0)
    report_period_start: date | None = None
    report_period_end: date | None = None
    external_observation_key: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def _cohort_individual_rules(self) -> CaseCreate:
        if self.observation_kind == ObservationKind.INDIVIDUAL:
            if self.cohort_size != 1:
                raise ValueError("individual observations require cohort_size == 1")
            if self.cohort_deaths != 0:
                raise ValueError("individual observations use death_date, not cohort_deaths")
        return self


class CaseUpdate(BaseModel):
    """Partial update for researcher PATCH; omitted fields stay unchanged."""

    patient_identifier: str | None = None
    symptom_onset_date: date | None = None
    hospitalization_date: date | None = None
    death_date: date | None = None
    location_country: str | None = Field(default=None, min_length=2, max_length=2)
    location_airport_code: str | None = Field(default=None, min_length=3, max_length=3)
    confirmed_or_suspected: CaseStatus | None = None
    lab_test_result: LabResult | None = None
    contacts: list[dict[str, Any]] | None = None
    data_source: str | None = None
    updated_by: str = "api"
    updated_reason: str = "case_update"
    observation_kind: ObservationKind | None = None
    cohort_size: int | None = Field(default=None, ge=1)
    cohort_deaths: int | None = Field(default=None, ge=0)
    report_period_start: date | None = None
    report_period_end: date | None = None

    @model_validator(mode="after")
    def _partial_cohort_rules(self) -> CaseUpdate:
        kind = self.observation_kind
        if kind == ObservationKind.INDIVIDUAL:
            if self.cohort_size is not None and self.cohort_size != 1:
                raise ValueError("individual observations require cohort_size == 1")
            if self.cohort_deaths is not None and self.cohort_deaths != 0:
                raise ValueError("individual observations use death_date, not cohort_deaths")
        return self


def assert_case_cohort_consistency(record: CaseRecord) -> None:
    """Shared cohort vs individual rules after create or merge."""

    if record.observation_kind == ObservationKind.INDIVIDUAL:
        if record.cohort_size != 1:
            raise ValueError("individual observations require cohort_size == 1")
        if record.cohort_deaths != 0:
            raise ValueError("individual observations use death_date, not cohort_deaths")


class CaseIngestionResponse(BaseModel):
    case: CaseRecord
    validation: ValidationResult
    accepted_for_inference: bool


class CaseSummary(BaseModel):
    total_confirmed: int
    total_suspected: int
    total_deaths: int
    last_updated: datetime
    data_sources: dict[str, int]
    external_feed_last_checked_at: datetime | None = None
    # Baseline scenario, horizon final day (typ. day 14), when a cached forecast exists
    forecast_deaths_median_14d: float | None = None
    forecast_deaths_ci_95_lower_14d: float | None = None
    forecast_deaths_ci_95_upper_14d: float | None = None


class ValidationResult(BaseModel):
    case_id: UUID
    checks_passed: dict[str, bool]
    issues: list[str]
    quality_score: float = Field(ge=0, le=1)
    quarantine_status: str


class ParameterEstimate(BaseModel):
    mean: float
    std: float
    ci_95: tuple[float, float]


class InferenceResult(BaseModel):
    version: str
    timestamp: datetime
    trigger: TriggerType
    n_cases: int
    parameters: dict[str, ParameterEstimate]
    diagnostics: dict[str, Any]
    posterior_download_url: str


class ForecastPoint(BaseModel):
    day: int
    date: date
    cases_cumulative: dict[str, float]
    cases_new: dict[str, float]
    deaths_cumulative: dict[str, float]


class ForecastResponse(BaseModel):
    scenario: str
    forecast: list[ForecastPoint]
    metadata: dict[str, Any]
    comparison_to_previous_version: dict[str, Any] | None = None


class ForecastRunRequest(BaseModel):
    scenarios: list[str] = Field(default_factory=lambda: ["baseline"])
    model_version: str = "latest"
    n_simulations: int = Field(default=10000, ge=100, le=100000)


class ForecastRunItem(BaseModel):
    forecast_id: UUID
    scenario: str
    model_version: str
    n_simulations: int
    execution_time_seconds: float
    cases_day_14: dict[str, float]
    deaths_day_14: dict[str, float]


class ForecastRunResponse(BaseModel):
    run_status: str
    forecasts: list[ForecastRunItem]


class ScenarioComparisonItem(BaseModel):
    name: str
    cases_by_day_14: dict[str, Any]
    deaths_by_day_14: dict[str, Any]
    vs_baseline: dict[str, Any] | None = None


class ScenarioComparison(BaseModel):
    comparison_date: date
    scenarios: list[ScenarioComparisonItem]
    # Requested scenario ids with no cached forecast (partial compare still returns 200).
    scenarios_unavailable: list[str] = Field(default_factory=list)


class QualityAlert(BaseModel):
    alert_id: str
    severity: str
    case_id: UUID
    issue: str
    timestamp: datetime
    recommended_action: str
