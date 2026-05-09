"""Vectorized SEIR+D agent-based model (FR-3.2).

State is held as a uint8 vector of size ``n_agents`` with codes
``S=0, E=1, I=2, R=3, D=4``. Per-agent ``e_remaining`` and ``i_remaining``
day-counters drive E->I and I->{R,D} transitions; transmissions are computed
in one matrix-vector product per day against the contact-network adjacency.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from eosp.services.geo_buckets import geo_bucket_spec
from eosp.services.network import ContactNetwork


STATE_S = 0
STATE_E = 1
STATE_I = 2
STATE_R = 3
STATE_D = 4
N_STATES = 5


@dataclass
class Trajectory:
    n_days: int
    daily_counts: np.ndarray  # shape (n_days + 1, 5)
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


def empty_seed() -> SeedState:
    return SeedState(exposed=[], infectious=[], recovered=[], deceased=[])


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

    geo_labels, agent_bucket = geo_bucket_spec(network)
    n_geo_buckets = len(geo_labels)
    bucket_cumulative_infected: np.ndarray | None = None
    bucket_infectious_I_arr: np.ndarray | None = None
    if n_geo_buckets:
        bucket_cumulative_infected = np.zeros((n_days + 1, n_geo_buckets), dtype=np.int32)
        bucket_infectious_I_arr = np.zeros((n_days + 1, n_geo_buckets), dtype=np.int32)

    state = np.full(n_agents, STATE_S, dtype=np.int8)
    e_remaining = np.zeros(n_agents, dtype=np.int16)
    i_remaining = np.zeros(n_agents, dtype=np.int16)

    p_transmit = float(params.get("p_transmit", 0.08))
    h2h_multiplier = float(params.get("h2h_multiplier", 1.0))
    cfr = float(np.clip(params.get("cfr", 0.40), 0.0, 1.0))
    incubation_mean = max(1.0, float(params.get("incubation_mean", 8.0)))
    infectious_scale = max(0.5, float(params.get("infectious_duration", 1.0)))

    for index in seed.exposed:
        if 0 <= index < n_agents:
            state[index] = STATE_E
            e_remaining[index] = max(1, int(round(rng.gamma(2.0, incubation_mean / 2.0))))
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

    daily_counts = np.zeros((n_days + 1, N_STATES), dtype=np.int32)
    new_cases_per_day = np.zeros(n_days + 1, dtype=np.int32)
    daily_counts[0] = _state_counts(state)
    new_cases_per_day[0] = int(daily_counts[0, STATE_E] + daily_counts[0, STATE_I] + daily_counts[0, STATE_R] + daily_counts[0, STATE_D])
    _fill_bucket_infected(state, agent_bucket, bucket_cumulative_infected, 0)
    _fill_bucket_infectious_I(state, agent_bucket, bucket_infectious_I_arr, 0)

    for day in range(1, n_days + 1):
        exposed_mask = state == STATE_E
        e_remaining[exposed_mask] -= 1
        ready_for_i = exposed_mask & (e_remaining <= 0)
        n_new_i = int(ready_for_i.sum())
        if n_new_i:
            durations = np.maximum(1, np.round(rng.weibull(1.5, size=n_new_i) * infectious_scale)).astype(np.int16)
            i_remaining[ready_for_i] = durations
            state[ready_for_i] = STATE_I

        infectious_now_mask = state == STATE_I
        adjacency = network.adjacency_for_day(day - 1)
        if adjacency.nnz:
            infectious_vector = infectious_now_mask.astype(np.float32)
            contact_pressure = np.asarray(adjacency @ infectious_vector).reshape(-1)
            exposure_probability = 1.0 - np.exp(-p_transmit * h2h_multiplier * contact_pressure)
        else:
            exposure_probability = np.zeros(n_agents, dtype=np.float32)

        susceptible_mask = state == STATE_S
        draws = rng.random(n_agents)
        new_exposures = susceptible_mask & (draws < exposure_probability)
        n_new_e = int(new_exposures.sum())
        if n_new_e:
            incubation_draws = rng.gamma(2.0, incubation_mean / 2.0, size=n_new_e)
            timers = np.maximum(1, np.round(incubation_draws)).astype(np.int16)
            e_remaining[new_exposures] = timers
            state[new_exposures] = STATE_E

        i_remaining[infectious_now_mask] -= 1
        ending_i = infectious_now_mask & (i_remaining <= 0)
        if ending_i.any():
            outcome_draws = rng.random(int(ending_i.sum()))
            deaths_among_ending = outcome_draws < cfr
            ending_indices = np.flatnonzero(ending_i)
            state[ending_indices[deaths_among_ending]] = STATE_D
            state[ending_indices[~deaths_among_ending]] = STATE_R

        daily_counts[day] = _state_counts(state)
        new_cases_per_day[day] = n_new_e
        _fill_bucket_infected(state, agent_bucket, bucket_cumulative_infected, day)
        _fill_bucket_infectious_I(state, agent_bucket, bucket_infectious_I_arr, day)

    cumulative_cases = (
        daily_counts[:, STATE_E]
        + daily_counts[:, STATE_I]
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
    infectious_mask = state == STATE_I
    n_b = out.shape[1]
    for b in range(n_b):
        mask = infectious_mask & (agent_bucket == b)
        out[day, b] = int(mask.sum())


def _state_counts(state: np.ndarray) -> np.ndarray:
    counts = np.zeros(N_STATES, dtype=np.int32)
    bin_counts = np.bincount(state, minlength=N_STATES)
    counts[: bin_counts.shape[0]] = bin_counts[:N_STATES]
    return counts
