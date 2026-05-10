"""Daily hub timeline kernel: loads, NB contacts, bursts, mobility, tallies."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np

from eosp.core.models import CaseRecord, ObservationKind
from eosp.services.hub_timeline.agents import AgentKind, AgentState, step_mobility_synthetic
from eosp.services.hub_timeline.config import HubTimelineSimulatorConfig
from eosp.services.hub_timeline.contacts import draw_contact_count, transmission_probability
from eosp.services.hub_timeline.disease_clock import sample_e_and_p_remaining, sample_weibull_duration
from eosp.services.hub_timeline.routes_graph import sample_home_airport


@dataclass
class WorldGraphBundle:
    allowed: frozenset[str]
    outbound: dict[str, dict[str, int]]
    dest_weights: Any  # Counter[str]
    iata_to_country: dict[str, str]


@dataclass
class _Agent:
    kind: AgentKind
    state: int | None
    iata: str
    home: str
    away: bool
    iata_end_prev: str
    e_rem: int
    p_rem: int
    i_rem: int
    h_rem: int
    onset: date | None
    hosp: date | None
    death: date | None


@dataclass
class HubTrajectorySnapshot:
    dates: list[date]
    hubs: list[str]
    hub_index: dict[str, int]
    cumulative_infected: np.ndarray  # (n_days, n_hubs)
    infectious_A: np.ndarray
    peak_unweighted_PI: np.ndarray  # (n_hubs,)
    global_cases: np.ndarray
    global_deaths: np.ndarray


def _draw_trunc_normal(rng: np.random.Generator, mean: float, std: float, *, lo: float, hi: float) -> float:
    return float(np.clip(rng.normal(float(mean), float(std)), lo, hi))


def _spawn_synthetic(
    rng: np.random.Generator,
    hub: str,
    world: WorldGraphBundle,
    cfg: HubTimelineSimulatorConfig,
    incubation_mean: float,
) -> _Agent:
    home = sample_home_airport(
        rng,
        hub,
        dest_weights=world.dest_weights,
        allowed=world.allowed,
        p_home_here=0.25,
    )
    hub_u = str(hub).upper()
    e_r, p_r = sample_e_and_p_remaining(
        rng,
        incubation_mean=incubation_mean,
        gamma_shape=cfg.latent_gamma_shape,
        mean_scale=cfg.latent_mean_scale,
        p_share_mean=cfg.p_fraction_mean,
    )
    return _Agent(
        kind=AgentKind.SYNTHETIC,
        state=int(AgentState.E),
        iata=hub_u,
        home=home,
        away=False,
        iata_end_prev="",
        e_rem=e_r,
        p_rem=p_r,
        i_rem=0,
        h_rem=0,
        onset=None,
        hosp=None,
        death=None,
    )


def _spawn_cohort_symptomatic(
    rng: np.random.Generator,
    hub: str,
    cfg: HubTimelineSimulatorConfig,
) -> _Agent:
    hub_u = str(hub).upper()
    i_r = sample_weibull_duration(
        rng,
        mean=cfg.i_duration_mean,
        shape=cfg.i_weibull_shape,
    )
    return _Agent(
        kind=AgentKind.SYNTHETIC,
        state=int(AgentState.I),
        iata=hub_u,
        home=hub_u,
        away=False,
        iata_end_prev="",
        e_rem=0,
        p_rem=0,
        i_rem=i_r,
        h_rem=0,
        onset=None,
        hosp=None,
        death=None,
    )


def _load_eff(agent: _Agent, cfg: HubTimelineSimulatorConfig) -> float:
    if agent.state is None:
        return 0.0
    st = int(agent.state)
    if st == int(AgentState.E) or st == int(AgentState.R) or st == int(AgentState.D):
        return 0.0
    if st == int(AgentState.P):
        return float(cfg.k_presympt)
    if st == int(AgentState.I):
        return 1.0
    if st == int(AgentState.H):
        return float(cfg.eps_hosp)
    return 0.0


def _weight_A(agent: _Agent, cfg: HubTimelineSimulatorConfig) -> float:
    if agent.state is None:
        return 0.0
    st = int(agent.state)
    if st == int(AgentState.P):
        return float(cfg.k_presympt)
    if st == int(AgentState.I):
        return 1.0
    return 0.0


def _counts_cumulative_stock(agent: _Agent) -> bool:
    if agent.state is None:
        return False
    return int(agent.state) in {
        int(AgentState.E),
        int(AgentState.P),
        int(AgentState.I),
        int(AgentState.H),
        int(AgentState.R),
        int(AgentState.D),
    }


def _mobility_frozen(agent: _Agent) -> bool:
    if agent.state is None:
        return True
    st = int(agent.state)
    if st in (int(AgentState.R), int(AgentState.D), int(AgentState.H)):
        return True
    if agent.kind == AgentKind.FIXED:
        return True
    return False


def simulate_trajectory(
    eligible_cases: list[CaseRecord],
    *,
    inference_params: dict[str, tuple[float, float]],
    world: WorldGraphBundle,
    config: HubTimelineSimulatorConfig,
    rng: np.random.Generator,
    sim_days: list[date],
) -> HubTrajectorySnapshot:
    """Run one stochastic trajectory over ``sim_days`` (length ``horizon_days``)."""

    n_days = len(sim_days)
    if n_days == 0:
        raise ValueError("sim_days empty")

    p_tr = _draw_trunc_normal(
        rng,
        inference_params["p_transmit"][0],
        inference_params["p_transmit"][1],
        lo=1e-9,
        hi=1.0,
    )
    h2h = _draw_trunc_normal(
        rng,
        inference_params["h2h_multiplier"][0],
        inference_params["h2h_multiplier"][1],
        lo=1e-9,
        hi=1e6,
    )
    cfr = _draw_trunc_normal(
        rng,
        inference_params["cfr"][0],
        inference_params["cfr"][1],
        lo=1e-9,
        hi=1.0,
    )
    incub_mean = float(inference_params["incubation"][0])

    agents: list[_Agent] = []
    for c in eligible_cases:
        if c.observation_kind != ObservationKind.INDIVIDUAL:
            continue
        hub = str(c.location_airport_code or "").upper()
        agents.append(
            _Agent(
                kind=AgentKind.FIXED,
                state=None,
                iata=hub,
                home=hub,
                away=False,
                iata_end_prev=hub,
                e_rem=0,
                p_rem=0,
                i_rem=0,
                h_rem=0,
                onset=c.symptom_onset_date,
                hosp=c.hospitalization_date,
                death=c.death_date,
            )
        )

    hubs_set: set[str] = set(world.allowed)
    for c in eligible_cases:
        if c.location_airport_code:
            hubs_set.add(str(c.location_airport_code).upper())
    hubs = sorted(hubs_set)
    hub_index = {h: i for i, h in enumerate(hubs)}
    n_h = len(hubs)
    cum = np.zeros((n_days, n_h), dtype=float)
    a_ar = np.zeros((n_days, n_h), dtype=float)
    peak_pi = np.zeros(n_h, dtype=float)
    g_cases = np.zeros(n_days, dtype=float)
    g_deaths = np.zeros(n_days, dtype=float)

    def hub_L() -> dict[str, float]:
        L: dict[str, float] = defaultdict(float)
        for ag in agents:
            w = _load_eff(ag, config)
            if w <= 0:
                continue
            L[ag.iata] += w
        return L

    def try_infect(hub: str, Lmap: dict[str, float]) -> None:
        load = float(Lmap.get(hub, 0.0))
        p_inf = transmission_probability(p_tr, h2h, load, alpha_load=config.alpha_load)
        if rng.random() < p_inf:
            agents.append(_spawn_synthetic(rng, hub, world, config, incub_mean))

    def run_generators(Lmap: dict[str, float], *, arrival_by_id: dict[int, bool]) -> None:
        for ag in agents:
            wgen = _load_eff(ag, config)
            if wgen <= 0 or ag.state is None:
                continue
            if ag.kind == AgentKind.FIXED:
                if int(ag.state) not in (int(AgentState.I), int(AgentState.H)):
                    continue
                if int(ag.state) == int(AgentState.H) and config.eps_hosp <= 1e-9:
                    continue
                mu = config.mu_stay
            else:
                mu = config.mu_travel if arrival_by_id.get(id(ag), True) else config.mu_stay
            n_ct = draw_contact_count(rng, mu, config.r_nb)
            for _ in range(n_ct):
                try_infect(ag.iata, Lmap)

    def onset_burst_today(d: date, Lmap: dict[str, float]) -> None:
        for c in eligible_cases:
            if c.symptom_onset_date != d:
                continue
            hub = str(c.location_airport_code or "").upper()
            if hub not in hub_index:
                continue
            n_b = draw_contact_count(rng, config.mu_onset_burst, config.r_nb)
            for _ in range(n_b):
                try_infect(hub, Lmap)

    for day_i, d in enumerate(sim_days):
        # Cohort onset: spawn ``cohort_size`` infectious synthetics at hub
        for c in eligible_cases:
            if c.observation_kind != ObservationKind.COHORT:
                continue
            if c.symptom_onset_date != d:
                continue
            hub = str(c.location_airport_code or "").upper()
            if hub not in hub_index:
                continue
            for _ in range(int(c.cohort_size)):
                agents.append(_spawn_cohort_symptomatic(rng, hub, config))

        for ag in agents:
            if ag.kind != AgentKind.FIXED or ag.state is not None:
                continue
            if ag.onset is not None and d == ag.onset:
                ag.state = int(AgentState.I)
                if ag.hosp is not None and ag.hosp > d:
                    ag.i_rem = max(500, sample_weibull_duration(
                        rng,
                        mean=config.i_duration_mean,
                        shape=config.i_weibull_shape,
                    ))
                else:
                    ag.i_rem = sample_weibull_duration(
                        rng,
                        mean=config.i_duration_mean,
                        shape=config.i_weibull_shape,
                    )

        for ag in agents:
            if ag.death is not None and d == ag.death and ag.state is not None:
                if int(ag.state) != int(AgentState.D):
                    ag.state = int(AgentState.D)

        for ag in agents:
            if ag.kind != AgentKind.FIXED or ag.state is None:
                continue
            if ag.hosp is not None and d == ag.hosp and int(ag.state) == int(AgentState.I):
                ag.state = int(AgentState.H)
                ag.h_rem = sample_weibull_duration(
                    rng,
                    mean=config.h_duration_mean,
                    shape=config.h_weibull_shape,
                )

        Lmap = hub_L()

        arrival_by_id: dict[int, bool] = {}
        for ag in agents:
            if ag.kind != AgentKind.SYNTHETIC or ag.state is None:
                continue
            arrival_by_id[id(ag)] = (not ag.iata_end_prev) or (ag.iata != ag.iata_end_prev)

        run_generators(Lmap, arrival_by_id=arrival_by_id)
        onset_burst_today(d, hub_L())

        for ag in agents:
            if ag.state is None:
                continue
            st = int(ag.state)
            if st == int(AgentState.D):
                continue
            if ag.kind == AgentKind.SYNTHETIC:
                if st == int(AgentState.E):
                    ag.e_rem -= 1
                    if ag.e_rem <= 0:
                        ag.state = int(AgentState.P)
                elif st == int(AgentState.P):
                    ag.p_rem -= 1
                    if ag.p_rem <= 0:
                        ag.state = int(AgentState.I)
                        ag.i_rem = sample_weibull_duration(
                            rng,
                            mean=config.i_duration_mean,
                            shape=config.i_weibull_shape,
                        )
                elif st == int(AgentState.I):
                    ag.i_rem -= 1
                    if ag.i_rem <= 0:
                        if rng.random() < float(config.p_hosp):
                            ag.state = int(AgentState.H)
                            ag.h_rem = sample_weibull_duration(
                                rng,
                                mean=config.h_duration_mean,
                                shape=config.h_weibull_shape,
                            )
                        else:
                            ag.state = int(AgentState.D) if rng.random() < cfr else int(AgentState.R)
                elif st == int(AgentState.H):
                    ag.h_rem -= 1
                    if ag.h_rem <= 0:
                        ag.state = int(AgentState.D) if rng.random() < cfr else int(AgentState.R)
            else:
                if st == int(AgentState.I):
                    if ag.hosp is not None and d < ag.hosp:
                        pass
                    else:
                        ag.i_rem -= 1
                        if ag.i_rem <= 0:
                            ag.state = int(AgentState.D) if rng.random() < cfr else int(AgentState.R)
                elif st == int(AgentState.H):
                    ag.h_rem -= 1
                    if ag.h_rem <= 0:
                        ag.state = int(AgentState.D) if rng.random() < cfr else int(AgentState.R)

        for ag in agents:
            if _mobility_frozen(ag):
                continue
            new_i, new_aw = step_mobility_synthetic(
                rng,
                iata_current=ag.iata,
                iata_home=ag.home,
                away=ag.away,
                outbound=world.outbound,
                cfg=config,
            )
            ag.iata = new_i
            ag.away = new_aw

        for hi, hcode in enumerate(hubs):
            cst = sum(1.0 for ag in agents if ag.iata == hcode and _counts_cumulative_stock(ag))
            cum[day_i, hi] = cst
            aw = sum(_weight_A(ag, config) for ag in agents if ag.iata == hcode)
            a_ar[day_i, hi] = aw
            pi_ct = sum(
                1.0
                for ag in agents
                if ag.iata == hcode
                and ag.state is not None
                and int(ag.state) in (int(AgentState.P), int(AgentState.I))
            )
            peak_pi[hi] = max(peak_pi[hi], pi_ct)

        g_cases[day_i] = float(
            sum(
                1.0
                for ag in agents
                if ag.state is not None
                and int(ag.state)
                in (
                    int(AgentState.E),
                    int(AgentState.P),
                    int(AgentState.I),
                    int(AgentState.H),
                    int(AgentState.R),
                    int(AgentState.D),
                )
            )
        )
        g_deaths[day_i] = float(
            sum(1.0 for ag in agents if ag.state is not None and int(ag.state) == int(AgentState.D))
        )

        for ag in agents:
            if ag.kind == AgentKind.SYNTHETIC and ag.state is not None:
                ag.iata_end_prev = ag.iata

    return HubTrajectorySnapshot(
        dates=list(sim_days),
        hubs=hubs,
        hub_index=hub_index,
        cumulative_infected=cum,
        infectious_A=a_ar,
        peak_unweighted_PI=peak_pi,
        global_cases=g_cases,
        global_deaths=g_deaths,
    )
