from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from eosp.services.mobility import MobilitySchedule


@dataclass
class MetapopParams:
    beta_local: float
    sigma: float
    gamma: float
    travel_frac_exposed: float = 1.0
    travel_frac_infectious: float = 1.0


@dataclass
class Trajectory:
    S: np.ndarray
    E: np.ndarray
    I: np.ndarray
    R: np.ndarray


def _local_seir_step(
    S: np.ndarray,
    E: np.ndarray,
    I: np.ndarray,
    R: np.ndarray,
    params: MetapopParams,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    n = S.shape[0]
    Sf = S.astype(np.float64, copy=False)
    Ef = E.astype(np.float64, copy=False)
    If = I.astype(np.float64, copy=False)
    Rf = R.astype(np.float64, copy=False)
    N = Sf + Ef + If + Rf

    infection_prob = 1.0 - np.exp(-params.beta_local * If / np.maximum(N, 1e-9))
    infection_prob = np.clip(infection_prob, 0.0, 1.0)

    new_e = np.zeros(n, dtype=np.int64)
    i_new = np.zeros(n, dtype=np.int64)
    r_new = np.zeros(n, dtype=np.int64)
    for k in range(n):
        sk = int(Sf[k])
        if sk < 0:
            sk = 0
        new_e[k] = rng.binomial(sk, float(infection_prob[k]))
    Sf = Sf - new_e
    Ef = Ef + new_e
    for k in range(n):
        ek = int(Ef[k])
        if ek < 0:
            ek = 0
        i_new[k] = rng.binomial(ek, min(float(params.sigma), 1.0))
        ik = int(If[k])
        if ik < 0:
            ik = 0
        r_new[k] = rng.binomial(ik, min(float(params.gamma), 1.0))
    Ef = Ef - i_new
    If = If + i_new - r_new
    Rf = Rf + r_new
    return Sf, Ef, If, Rf


def _mobility_step(
    S: np.ndarray,
    E: np.ndarray,
    I: np.ndarray,
    R: np.ndarray,
    schedule: MobilitySchedule,
    day_idx: int,
    params: MetapopParams,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    n_patches = S.shape[0]
    flows = schedule.day_flows[day_idx]
    total_out = schedule.total_outflow_per_patch_day[day_idx]

    delta_E = np.zeros((n_patches, n_patches), dtype=np.float64)
    delta_I = np.zeros((n_patches, n_patches), dtype=np.float64)

    Ef = E.astype(np.float64, copy=True)
    If = I.astype(np.float64, copy=True)

    for i in range(n_patches):
        o_i = float(total_out[i])
        if o_i <= 0.0:
            continue

        e_i = float(Ef[i])
        i_i = float(If[i])
        max_e_out = e_i * float(params.travel_frac_exposed)
        max_i_out = i_i * float(params.travel_frac_infectious)
        pool = max_e_out + max_i_out
        if pool <= 0.0:
            continue

        cap = min(o_i, pool)
        cap_n = int(np.floor(cap + 1e-12))
        if cap_n <= 0:
            continue

        dests: list[int] = []
        weights: list[float] = []
        for (ii, j), m_ij in flows.items():
            if ii != i or j == i:
                continue
            if m_ij > 0.0:
                dests.append(j)
                weights.append(float(m_ij))

        if not dests:
            continue

        w = np.array(weights, dtype=np.float64)
        p = w / np.sum(w)
        alloc = rng.multinomial(cap_n, p)

        frac_exposed = max_e_out / pool

        for idx, j in enumerate(dests):
            a = int(alloc[idx])
            if a <= 0:
                continue
            move_e = int(rng.binomial(a, min(frac_exposed, 1.0)))
            move_i = a - move_e
            delta_E[i, j] += move_e
            delta_I[i, j] += move_i

        e_row = float(np.sum(delta_E[i, :]))
        i_row = float(np.sum(delta_I[i, :]))
        s_e = 1.0 if e_row <= e_i + 1e-9 else e_i / max(e_row, 1e-15)
        s_i = 1.0 if i_row <= i_i + 1e-9 else i_i / max(i_row, 1e-15)
        if s_e < 1.0 - 1e-15 or s_i < 1.0 - 1e-15:
            delta_E[i, :] *= s_e
            delta_I[i, :] *= s_i

    e_out = np.sum(delta_E, axis=1)
    e_in = np.sum(delta_E, axis=0)
    i_out = np.sum(delta_I, axis=1)
    i_in = np.sum(delta_I, axis=0)

    Ef = Ef - e_out + e_in
    If = If - i_out + i_in
    return S, Ef, If, R


def simulate_metapop_once(
    schedule: MobilitySchedule,
    params: MetapopParams,
    init_s: np.ndarray,
    init_e: np.ndarray,
    init_i: np.ndarray,
    init_r: np.ndarray,
    rng: np.random.Generator,
) -> Trajectory:
    n_patches = len(schedule.patch_ids)
    n_days = schedule.n_days
    for i, name in enumerate((init_s, init_e, init_i, init_r)):
        arr = np.asarray(name, dtype=np.float64)
        if arr.shape != (n_patches,):
            raise ValueError(f"init state {i} must have shape ({n_patches},), got {arr.shape}")

    out_S = np.empty((n_days + 1, n_patches), dtype=np.float64)
    out_E = np.empty((n_days + 1, n_patches), dtype=np.float64)
    out_I = np.empty((n_days + 1, n_patches), dtype=np.float64)
    out_R = np.empty((n_days + 1, n_patches), dtype=np.float64)

    S = np.asarray(init_s, dtype=np.float64).copy()
    E = np.asarray(init_e, dtype=np.float64).copy()
    I = np.asarray(init_i, dtype=np.float64).copy()
    R = np.asarray(init_r, dtype=np.float64).copy()

    out_S[0] = S
    out_E[0] = E
    out_I[0] = I
    out_R[0] = R

    for d in range(n_days):
        S, E, I, R = _local_seir_step(S, E, I, R, params, rng)
        S, E, I, R = _mobility_step(S, E, I, R, schedule, d, params, rng)
        out_S[d + 1] = S
        out_E[d + 1] = E
        out_I[d + 1] = I
        out_R[d + 1] = R

    return Trajectory(S=out_S, E=out_E, I=out_I, R=out_R)


def run_ensemble_metapop(
    *,
    schedule: MobilitySchedule,
    params: MetapopParams,
    init_s: np.ndarray,
    init_e: np.ndarray,
    init_i: np.ndarray,
    init_r: np.ndarray,
    n_runs: int,
    rng_seed: int,
) -> dict:
    if n_runs < 1:
        raise ValueError("n_runs must be >= 1")

    n_patches = len(schedule.patch_ids)
    n_days = schedule.n_days
    stack_I = np.empty((n_runs, n_days + 1, n_patches), dtype=np.float64)

    for r in range(n_runs):
        ss = np.random.SeedSequence([int(rng_seed), r])
        rng = np.random.default_rng(ss)
        traj = simulate_metapop_once(
            schedule=schedule,
            params=params,
            init_s=init_s,
            init_e=init_e,
            init_i=init_i,
            init_r=init_r,
            rng=rng,
        )
        stack_I[r] = traj.I

    p_lo, p_med, p_hi = np.percentile(stack_I, [2.5, 50.0, 97.5], axis=0)

    patches: list[dict] = []
    for p_idx, code in enumerate(schedule.patch_ids):
        patches.append(
            {
                "code": code,
                "i_median_by_day": p_med[:, p_idx].astype(float).tolist(),
                "i_p2_5_by_day": p_lo[:, p_idx].astype(float).tolist(),
                "i_p97_5_by_day": p_hi[:, p_idx].astype(float).tolist(),
            }
        )

    return {"patches": patches, "n_runs": int(n_runs)}


def run_ensemble_metapop(
    *,
    schedule: MobilitySchedule,
    params: MetapopParams,
    init_s: np.ndarray,
    init_e: np.ndarray,
    init_i: np.ndarray,
    init_r: np.ndarray,
    n_runs: int,
    rng_seed: int,
) -> dict:
    if n_runs < 1:
        raise ValueError("n_runs must be >= 1")

    n_patches = len(schedule.patch_ids)
    n_days = schedule.n_days
    stack_I = np.empty((n_runs, n_days + 1, n_patches), dtype=np.float64)

    for r in range(n_runs):
        ss = np.random.SeedSequence([int(rng_seed), r])
        rng = np.random.default_rng(ss)
        traj = simulate_metapop_once(
            schedule=schedule,
            params=params,
            init_s=init_s,
            init_e=init_e,
            init_i=init_i,
            init_r=init_r,
            rng=rng,
        )
        stack_I[r] = traj.I

    p_lo, p_med, p_hi = np.percentile(stack_I, [2.5, 50.0, 97.5], axis=0)

    patches: list[dict] = []
    for p_idx, code in enumerate(schedule.patch_ids):
        patches.append(
            {
                "code": code,
                "i_median_by_day": p_med[:, p_idx].astype(float).tolist(),
                "i_p2_5_by_day": p_lo[:, p_idx].astype(float).tolist(),
                "i_p97_5_by_day": p_hi[:, p_idx].astype(float).tolist(),
            }
        )

    return {"patches": patches, "n_runs": int(n_runs)}
