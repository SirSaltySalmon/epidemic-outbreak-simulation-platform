"""Dataclass configuration for hub timeline Monte Carlo (scenario + defaults)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any


@dataclass
class HubTimelineSimulatorConfig:
    """Tunable kernel parameters; merged from ``scenarios.json`` ``hub_timeline`` blocks."""

    mu_travel: float = 20.0
    mu_stay: float = 5.0
    mu_onset_burst: float = 20.0
    r_nb: float = 8.0
    alpha_load: float = 0.08
    k_presympt: float = 0.35
    eps_hosp: float = 0.02
    p_stay_when_at_home: float = 0.7
    p_return_when_away: float = 0.7
    horizon_days: int = 30
    latent_gamma_shape: float = 2.0
    latent_mean_scale: float = 1.0  # incubation mean maps to E+P total mean via gamma
    p_fraction_mean: float = 0.35  # expected share of incubation spent in P (rough)
    i_weibull_shape: float = 1.5
    i_duration_mean: float = 7.0
    i_duration_std: float = 2.0
    h_weibull_shape: float = 1.5
    h_duration_mean: float = 10.0
    p_hosp: float = 0.25
    default_cfr: float = 0.02
    default_cfr_hosp: float = 0.04
    baseline_contacts_daily: float = 4.0

    def merged_with_scenario_hub_timeline(self, hub_timeline: dict[str, Any] | None) -> HubTimelineSimulatorConfig:
        if not hub_timeline:
            return self
        cfg = replace(self)
        contacts = hub_timeline.get("contacts") or {}
        if "mu_travel" in contacts:
            cfg.mu_travel = float(contacts["mu_travel"])
        if "mu_stay" in contacts:
            cfg.mu_stay = float(contacts["mu_stay"])
        if "mu_onset_burst" in contacts:
            cfg.mu_onset_burst = float(contacts["mu_onset_burst"])
        if "r_dispersion" in contacts:
            cfg.r_nb = float(contacts["r_dispersion"])
        load = hub_timeline.get("load") or {}
        if "alpha" in load:
            cfg.alpha_load = float(load["alpha"])
        mob = hub_timeline.get("mobility") or {}
        if "p_stay_when_at_home" in mob:
            cfg.p_stay_when_at_home = float(mob["p_stay_when_at_home"])
        if "p_return_when_away" in mob:
            cfg.p_return_when_away = float(mob["p_return_when_away"])
        for key in (
            "k_presympt",
            "eps_hosp",
            "horizon_days",
            "p_hosp",
            "default_cfr",
            "default_cfr_hosp",
        ):
            if key in hub_timeline:
                setattr(cfg, key, float(hub_timeline[key]) if key != "horizon_days" else int(hub_timeline[key]))
        return cfg


def default_hub_timeline_config() -> HubTimelineSimulatorConfig:
    return HubTimelineSimulatorConfig()
