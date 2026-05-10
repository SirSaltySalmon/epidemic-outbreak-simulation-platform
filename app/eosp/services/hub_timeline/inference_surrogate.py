"""Deterministic hub-timeline features for Bayesian surrogate likelihood."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np

from eosp.core.models import CaseRecord, ObservationKind
from eosp.services.hub_timeline.config import HubTimelineSimulatorConfig


@dataclass(frozen=True)
class HubSurrogateFeatures:
    observed_onsets: np.ndarray
    fixed_load_by_day: np.ndarray
    fixed_contact_mu_by_day: np.ndarray
    generated_contact_mu: float
    presymptomatic_weight: float
    hospital_weight: float
    alpha_load: float
    incubation_delay_days: int
    generated_infectious_duration_days: int


def build_hub_surrogate_features(
    cases: list[CaseRecord],
    *,
    sim_days: list[date],
    config: HubTimelineSimulatorConfig,
) -> HubSurrogateFeatures:
    day_index = {d: i for i, d in enumerate(sim_days)}
    n = len(sim_days)
    observed = np.zeros(n, dtype=float)
    fixed_load = np.zeros(n, dtype=float)
    fixed_mu = np.zeros(n, dtype=float)

    for case in cases:
        onset_i = day_index.get(case.symptom_onset_date)
        if onset_i is not None:
            observed[onset_i] += float(case.cohort_size)
            fixed_mu[onset_i] += float(config.mu_onset_burst) * float(case.cohort_size)
        if case.observation_kind != ObservationKind.INDIVIDUAL:
            continue
        for i, current_day in enumerate(sim_days):
            if current_day < case.symptom_onset_date:
                continue
            if case.death_date is not None and current_day >= case.death_date:
                continue
            if case.hospitalization_date is not None and current_day >= case.hospitalization_date:
                fixed_load[i] += float(config.eps_hosp)
                fixed_mu[i] += float(config.mu_stay) * float(config.eps_hosp)
            else:
                fixed_load[i] += 1.0
                fixed_mu[i] += float(config.mu_stay)

    generated_mu = float(config.p_stay_when_at_home) * float(config.mu_stay) + (
        1.0 - float(config.p_stay_when_at_home)
    ) * float(config.mu_travel)
    delay = max(1, int(round(float(config.i_duration_mean) * float(config.p_fraction_mean))))
    infectious_days = max(1, int(round(float(config.i_duration_mean))))
    return HubSurrogateFeatures(
        observed_onsets=observed,
        fixed_load_by_day=fixed_load,
        fixed_contact_mu_by_day=fixed_mu,
        generated_contact_mu=generated_mu,
        presymptomatic_weight=float(config.k_presympt),
        hospital_weight=float(config.eps_hosp),
        alpha_load=float(config.alpha_load),
        incubation_delay_days=delay,
        generated_infectious_duration_days=infectious_days,
    )
