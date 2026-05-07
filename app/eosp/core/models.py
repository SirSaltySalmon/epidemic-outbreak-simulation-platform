from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class CaseStatus(StrEnum):
    CONFIRMED = "confirmed"
    SUSPECTED = "suspected"


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


class QualityAlert(BaseModel):
    alert_id: str
    severity: str
    case_id: UUID
    issue: str
    timestamp: datetime
    recommended_action: str
