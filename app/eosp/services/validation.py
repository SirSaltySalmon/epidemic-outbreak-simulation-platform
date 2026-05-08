from collections import Counter

from eosp.core.models import CaseRecord, ObservationKind, ValidationResult


OFFICIAL_SOURCES = {"WHO_DON", "Contact_Trace_DB"}
VALID_COUNTRIES = {"ZA", "CH", "NL", "ES", "GB", "CV"}


def validate_case(case: CaseRecord, all_cases: list[CaseRecord]) -> ValidationResult:
    temporal = _temporal_consistency(case)
    geography = case.location_country in VALID_COUNTRIES
    official = case.data_source in OFFICIAL_SOURCES
    duplicate_probability = _duplicate_probability(case, all_cases)

    score = (
        0.30 * float(official)
        + 0.25 * (1 - duplicate_probability)
        + 0.25 * float(temporal)
        + 0.20 * float(geography)
    )
    score = round(score, 2)

    issues: list[str] = []
    if not temporal:
        issues.append("Temporal inconsistency: expected symptom onset <= hospitalization <= death")
    if not geography:
        issues.append("Geographic plausibility failed: country not in configured outbreak geography")
    if duplicate_probability > 0:
        issues.append("Potential duplicate case detected")
    if score < 0.65:
        quarantine_status = "quarantined"
    elif score < 0.75:
        quarantine_status = "requires_review"
    else:
        quarantine_status = "approved"

    return ValidationResult(
        case_id=case.case_id,
        checks_passed={
            "official_source": official,
            "duplicate_detection": duplicate_probability == 0,
            "temporal_consistency": temporal,
            "geographic_plausibility": geography,
        },
        issues=issues,
        quality_score=score,
        quarantine_status=quarantine_status,
    )


def _temporal_consistency(case: CaseRecord) -> bool:
    if case.hospitalization_date and case.symptom_onset_date > case.hospitalization_date:
        return False
    if case.death_date and case.hospitalization_date and case.hospitalization_date > case.death_date:
        return False
    if case.death_date and case.symptom_onset_date > case.death_date:
        return False
    return True


def _duplicate_probability(case: CaseRecord, all_cases: list[CaseRecord]) -> float:
    if case.observation_kind == ObservationKind.COHORT and case.external_observation_key:
        n_dup = sum(1 for item in all_cases if item.external_observation_key == case.external_observation_key)
        return 0.85 if n_dup > 1 else 0.0
    key_counts = Counter((item.patient_identifier, item.symptom_onset_date) for item in all_cases)
    return 0.85 if key_counts[(case.patient_identifier, case.symptom_onset_date)] > 1 else 0.0

