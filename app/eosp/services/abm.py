"""Vectorized SEIPHR+D agent-based model (FR-3.2).

State is held as a uint8 vector of size ``n_agents`` with codes
``S=0, E=1, I=2, R=3, D=4, P=5, H=6``. ``E`` is strictly latent (no
transmission); ``P`` is presymptomatic infectious at ``k × β_sym`` relative
to symptomatic ``I``; ``H`` is hospitalized (mobility frozen; negligible
hospital transmission by default). Per-agent day counters drive
``E→P→I`` and ``I→{H,R,D}``, ``H→{R,D}``. Transmission uses either a sparse
contact-network adjacency (legacy spec-driven graph) or, when the network
carries ``itinerary_contact_patch``, patch / in-flight mass-action mixing
(Track B).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from eosp.services.geo_buckets import geo_bucket_spec
from eosp.services.network import ContactNetwork


STATE_S = 0
STATE_E = 1
STATE_I = 2
STATE_R = 3
STATE_D = 4
STATE_P = 5
STATE_H = 6
N_STATES = 7


@dataclass
class Trajectory:
    n_days: int
    daily_counts: np.ndarray  # shape (n_days + 1, N_STATES)
    cumulative_cases: np.ndarray  # shape (n_days + 1,)
    cumulative_deaths: np.ndarray  # shape (n_days + 1,)
    new_cases_per_day: np.ndarray  # shape (n_days + 1,)
    geo_bucket_labels: tuple[str, ...] = ()
    bucket_cumulative_infected: np.ndarray | None = None  # shape (n_days + 1, n_buckets)
    bucket_infectious_I: np.ndarray | None = None  # shape (n_days + 1, n_buckets)


@dataclass
class SeedState:
    """Initial state at simulation day 0."""

    exposed: list[int]
    infectious: list[int]
    recovered: list[int]
    deceased: list[int]
    presymptomatic: list[int] | None = None
    seed_scaling: dict[str, Any] | None = None


def empty_seed() -> SeedState:
    return SeedState(
        exposed=[],
        infectious=[],
        recovered=[],
        deceased=[],
        presymptomatic=None,
        seed_scaling=None,
    )


def simulate_trajectory(
    network: ContactNetwork,
    params: dict[str, float],
    seed: SeedState | None = None,
    n_days: int = 14,
    rng: np.random.Generator | None = None,
) -> Trajectory:
    rng = rng if rng is not None else np.random.default_rng()
    seed = seed or empty_seed()
    n_agents = network.n_agents

    use_itinerary = network.itinerary_contact_patch is not None
    patch_arr = network.itinerary_contact_patch
    flight_arr = network.itinerary_flight_group

    if use_itinerary:
        geo_labels = network.itinerary_bucket_labels or tuple()
        n_geo_buckets = len(geo_labels)
        static_agent_bucket: np.ndarray | None = None
    else:
        geo_labels, static_agent_bucket = geo_bucket_spec(network)
        n_geo_buckets = len(geo_labels)
    bucket_cumulative_infected: np.ndarray | None = None
    bucket_infectious_I_arr: np.ndarray | None = None
    if n_geo_buckets:
        bucket_cumulative_infected = np.zeros((n_days + 1, n_geo_buckets), dtype=np.int32)
        bucket_infectious_I_arr = np.zeros((n_days + 1, n_geo_buckets), dtype=np.int32)

    state = np.full(n_agents, STATE_S, dtype=np.int8)
    e_remaining = np.zeros(n_agents, dtype=np.int16)
    p_remaining = np.zeros(n_agents, dtype=np.int16)
    i_remaining = np.zeros(n_agents, dtype=np.int16)
    h_remaining = np.zeros(n_agents, dtype=np.int16)

    p_transmit = float(params.get("p_transmit", 0.08))
    k_presym = float(params.get("presym_transmit_ratio", params.get("k_presym", 0.5)))
    h2h_multiplier = float(params.get("h2h_multiplier", 1.0))
    cfr = float(np.clip(params.get("cfr", 0.40), 0.0, 1.0))
    cfr_H = float(np.clip(params.get("cfr_hospital", params.get("cfr_H", cfr * 0.5)), 0.0, 1.0))
    p_hosp = float(np.clip(params.get("p_hosp", 0.0), 0.0, 1.0))
    eps_hosp = float(params.get("hospital_transmit_rate", 1e-3))
    incubation_mean = max(1.0, float(params.get("incubation_mean", 8.0)))
    infectious_scale = max(0.5, float(params.get("infectious_duration", 1.0)))
    hospital_scale = max(0.5, float(params.get("hospital_duration_scale", infectious_scale)))

    for index in seed.exposed:
        if 0 <= index < n_agents:
            state[index] = STATE_E
            total = max(2, int(round(rng.gamma(2.0, incubation_mean / 2.0))))
            p_draw = max(1, int(round(rng.gamma(2.0, 1.0))))
            p_draw = min(p_draw, total - 1)
            e_remaining[index] = total - p_draw
            p_remaining[index] = p_draw
    for index in seed.infectious:
        if 0 <= index < n_agents:
            state[index] = STATE_I
            i_remaining[index] = max(1, int(round(rng.weibull(1.5) * infectious_scale)))
    for index in seed.recovered:
        if 0 <= index < n_agents:
            state[index] = STATE_R
    for index in seed.deceased:
        if 0 <= index < n_agents:
            state[index] = STATE_D
    if seed.presymptomatic:
        for index in seed.presymptomatic:
            if 0 <= index < n_agents:
                state[index] = STATE_P
                p_remaining[index] = max(1, int(round(rng.gamma(2.0, 1.0))))

    daily_counts = np.zeros((n_days + 1, N_STATES), dtype=np.int32)
    new_cases_per_day = np.zeros(n_days + 1, dtype=np.int32)
    daily_counts[0] = _state_counts(state)
    new_cases_per_day[0] = int(
        daily_counts[0, STATE_E]
        + daily_counts[0, STATE_P]
        + daily_counts[0, STATE_I]
        + daily_counts[0, STATE_H]
        + daily_counts[0, STATE_R]
        + daily_counts[0, STATE_D]
    )
    _fill_bucket_infected(state, _agent_buckets_for_day(network, static_agent_bucket, 0), bucket_cumulative_infected, 0)
    _fill_bucket_infectious_I(state, _agent_buckets_for_day(network, static_agent_bucket, 0), bucket_infectious_I_arr, 0)

    for day in range(1, n_days + 1):
        exposed_mask = state == STATE_E
        e_remaining[exposed_mask] -= 1
        ready_for_p = exposed_mask & (e_remaining <= 0)
        if ready_for_p.any():
            state[ready_for_p] = STATE_P

        presym_mask = state == STATE_P
        p_remaining[presym_mask] -= 1
        ready_for_i = presym_mask & (p_remaining <= 0)
        if ready_for_i.any():
            n_pi = int(ready_for_i.sum())
            durations = np.maximum(1, np.round(rng.weibull(1.5, size=n_pi) * infectious_scale)).astype(np.int16)
            i_remaining[ready_for_i] = durations
            state[ready_for_i] = STATE_I

        presym_mask = state == STATE_P
        infectious_now_mask = state == STATE_I
        hosp_mask = state == STATE_H
        if use_itinerary and patch_arr is not None:
            dloc = int(min(day - 1, patch_arr.shape[1] - 1))
            patches = patch_arr[:, dloc]
            fgroups = flight_arr[:, dloc] if flight_arr is not None else np.zeros(n_agents, dtype=np.int32)
            v = (
                presym_mask.astype(np.float32) * np.float32(k_presym)
                + infectious_now_mask.astype(np.float32)
                + hosp_mask.astype(np.float32) * np.float32(eps_hosp)
            )
            pw = float(network.itinerary_patch_weight)
            fw = float(network.itinerary_flight_weight)
            n_buck = max(1, n_geo_buckets)
            patch_ids = patches.astype(np.int64, copy=False)
            patch_load = np.bincount(patch_ids, weights=v, minlength=n_buck).astype(np.float32)
            contact_from_patch = patch_load[patch_ids] - v

            contact_from_flight = np.zeros(n_agents, dtype=np.float32)
            gpos = fgroups > 0
            if gpos.any():
                fg_int = fgroups.astype(np.int64, copy=False)
                max_g = int(fg_int.max()) + 1
                flight_load = np.bincount(fg_int, weights=v, minlength=max_g).astype(np.float32)
                contact_from_flight = flight_load[fg_int] - v

            pressure = np.where(
                fgroups > 0,
                fw * np.maximum(0.0, contact_from_flight),
                pw * np.maximum(0.0, contact_from_patch),
            )
            mix = float(params.get("contacts_daily", 2.4)) / 24.0
            exposure_probability = 1.0 - np.exp(-p_transmit * h2h_multiplier * mix * pressure)
        else:
            adjacency = network.adjacency_for_day(day - 1)
            if adjacency.nnz:
                v = (
                    presym_mask.astype(np.float32) * np.float32(k_presym)
                    + infectious_now_mask.astype(np.float32)
                    + hosp_mask.astype(np.float32) * np.float32(eps_hosp)
                )
                contact_pressure = np.asarray(adjacency @ v).reshape(-1)
                exposure_probability = 1.0 - np.exp(-p_transmit * h2h_multiplier * contact_pressure)
            else:
                exposure_probability = np.zeros(n_agents, dtype=np.float32)

        susceptible_mask = state == STATE_S
        draws = rng.random(n_agents)
        new_exposures = susceptible_mask & (draws < exposure_probability)
        n_new_e = int(new_exposures.sum())
        if n_new_e:
            totals = np.maximum(2, np.round(rng.gamma(2.0, incubation_mean / 2.0, size=n_new_e))).astype(np.int16)
            p_draws = np.maximum(1, np.round(rng.gamma(2.0, 1.0, size=n_new_e))).astype(np.int16)
            p_draws = np.minimum(p_draws, totals - 1)
            e_draws = totals - p_draws
            e_remaining[new_exposures] = e_draws
            p_remaining[new_exposures] = p_draws
            state[new_exposures] = STATE_E

        infectious_now_mask = state == STATE_I
        i_remaining[infectious_now_mask] -= 1
        ending_i = infectious_now_mask & (i_remaining <= 0)
        if ending_i.any():
            ending_indices = np.flatnonzero(ending_i)
            n_end = len(ending_indices)
            u = rng.random(n_end)
            to_hosp = u < p_hosp
            if to_hosp.any():
                hi = ending_indices[to_hosp]
                h_len = max(1, int(round(rng.weibull(1.5) * hospital_scale)))
                h_remaining[hi] = h_len
                state[hi] = STATE_H
            if (~to_hosp).any():
                rest = ending_indices[~to_hosp]
                outcome_draws = rng.random(len(rest))
                deaths_among = outcome_draws < cfr
                state[rest[deaths_among]] = STATE_D
                state[rest[~deaths_among]] = STATE_R

        hosp_mask = state == STATE_H
        h_remaining[hosp_mask] -= 1
        ending_h = hosp_mask & (h_remaining <= 0)
        if ending_h.any():
            h_indices = np.flatnonzero(ending_h)
            outcome_draws = rng.random(len(h_indices))
            deaths_among = outcome_draws < cfr_H
            state[h_indices[deaths_among]] = STATE_D
            state[h_indices[~deaths_among]] = STATE_R

        daily_counts[day] = _state_counts(state)
        new_cases_per_day[day] = n_new_e
        bd = _agent_buckets_for_day(network, static_agent_bucket, day)
        _fill_bucket_infected(state, bd, bucket_cumulative_infected, day)
        _fill_bucket_infectious_I(state, bd, bucket_infectious_I_arr, day)

    cumulative_cases = (
        daily_counts[:, STATE_E]
        + daily_counts[:, STATE_P]
        + daily_counts[:, STATE_I]
        + daily_counts[:, STATE_H]
        + daily_counts[:, STATE_R]
        + daily_counts[:, STATE_D]
    )
    cumulative_deaths = daily_counts[:, STATE_D]
    return Trajectory(
        n_days=n_days,
        daily_counts=daily_counts,
        cumulative_cases=cumulative_cases.astype(np.int32),
        cumulative_deaths=cumulative_deaths.astype(np.int32),
        new_cases_per_day=new_cases_per_day,
        geo_bucket_labels=geo_labels,
        bucket_cumulative_infected=bucket_cumulative_infected,
        bucket_infectious_I=bucket_infectious_I_arr,
    )


def _agent_buckets_for_day(
    network: ContactNetwork,
    static_bucket: np.ndarray | None,
    day: int,
) -> np.ndarray:
    if network.itinerary_contact_patch is not None:
        arr = network.itinerary_contact_patch
        dloc = int(min(max(0, day), arr.shape[1] - 1))
        return arr[:, dloc]
    assert static_bucket is not None
    return static_bucket


def _fill_bucket_infected(
    state: np.ndarray,
    agent_bucket: np.ndarray,
    bucket_cumulative_infected: np.ndarray | None,
    day: int,
) -> None:
    if bucket_cumulative_infected is None:
        return
    infected = state != STATE_S
    n_b = bucket_cumulative_infected.shape[1]
    for b in range(n_b):
        mask = infected & (agent_bucket == b)
        bucket_cumulative_infected[day, b] = int(mask.sum())


def _fill_bucket_infectious_I(
    state: np.ndarray,
    agent_bucket: np.ndarray,
    out: np.ndarray | None,
    day: int,
) -> None:
    if out is None:
        return
    transmitting_mask = (state == STATE_I) | (state == STATE_P)
    n_b = out.shape[1]
    for b in range(n_b):
        mask = transmitting_mask & (agent_bucket == b)
        out[day, b] = int(mask.sum())


def _state_counts(state: np.ndarray) -> np.ndarray:
    counts = np.zeros(N_STATES, dtype=np.int32)
    bin_counts = np.bincount(state.astype(np.int64, copy=False), minlength=N_STATES)
    counts[: bin_counts.shape[0]] = bin_counts[:N_STATES]
    return counts
