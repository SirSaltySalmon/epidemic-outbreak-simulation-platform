from pathlib import Path

import numpy as np

from eosp.services.mobility import load_mobility_schedule
from eosp.services.metapop import MetapopParams, run_ensemble_metapop, simulate_metapop_once


def test_simulate_metapop_preserves_nonnegative():
    root = Path(__file__).resolve().parents[1]
    sched = load_mobility_schedule(root / "app" / "eosp" / "data" / "mobility_weekly_skeleton.json")
    rng = np.random.default_rng(0)
    params = MetapopParams(
        beta_local=0.3,
        sigma=0.35,
        gamma=0.2,
        travel_frac_exposed=1.0,
        travel_frac_infectious=1.0,
    )
    init_s = np.array([900.0, 5000.0, 5000.0, 8000.0])
    init_e = np.array([80.0, 0.0, 0.0, 0.0])
    init_i = np.array([20.0, 0.0, 0.0, 0.0])
    traj = simulate_metapop_once(
        schedule=sched,
        params=params,
        init_s=init_s,
        init_e=init_e,
        init_i=init_i,
        init_r=np.zeros(4),
        rng=rng,
    )
    n_patches = len(sched.patch_ids)
    assert traj.S.shape == (sched.n_days + 1, n_patches)
    assert traj.E.shape == (sched.n_days + 1, n_patches)
    assert traj.I.shape == (sched.n_days + 1, n_patches)
    assert traj.R.shape == (sched.n_days + 1, n_patches)
    assert np.all(traj.S >= -1e-9)
    assert np.all(traj.E >= -1e-9)
    assert np.all(traj.I >= -1e-9)
    assert np.all(traj.R >= -1e-9)


def test_secondary_hub_gets_infected_mass_without_direct_seed():
    """HUB2 (index 2) starts with no E/I; infection reaches it via HUB1."""
    root = Path(__file__).resolve().parents[1]
    sched = load_mobility_schedule(root / "app" / "eosp" / "data" / "mobility_weekly_skeleton.json")
    rng = np.random.default_rng(42)
    params = MetapopParams(
        beta_local=0.55,
        sigma=0.45,
        gamma=0.08,
        travel_frac_exposed=1.0,
        travel_frac_infectious=1.0,
    )
    init_s = np.array([200.0, 20000.0, 15000.0, 10000.0])
    init_e = np.array([60.0, 0.0, 0.0, 0.0])
    init_i = np.array([40.0, 0.0, 0.0, 0.0])
    init_r = np.zeros(4)
    traj = simulate_metapop_once(
        schedule=sched,
        params=params,
        init_s=init_s,
        init_e=init_e,
        init_i=init_i,
        init_r=init_r,
        rng=rng,
    )
    assert traj.E[0, 2] == 0.0 and traj.I[0, 2] == 0.0
    assert traj.E[-1, 2] + traj.I[-1, 2] > 0.05


def test_run_ensemble_metapop_percentiles():
    root = Path(__file__).resolve().parents[1]
    sched = load_mobility_schedule(root / "app" / "eosp" / "data" / "mobility_weekly_skeleton.json")
    params = MetapopParams(
        beta_local=0.35,
        sigma=0.3,
        gamma=0.15,
        travel_frac_exposed=1.0,
        travel_frac_infectious=1.0,
    )
    init_s = np.array([500.0, 8000.0, 7000.0, 6000.0])
    init_e = np.array([40.0, 0.0, 0.0, 0.0])
    init_i = np.array([10.0, 0.0, 0.0, 0.0])
    init_r = np.zeros(4)
    n_runs = 30
    progress_log: list[dict] = []

    def cb(ev: dict) -> None:
        progress_log.append(dict(ev))

    out = run_ensemble_metapop(
        schedule=sched,
        params=params,
        init_s=init_s,
        init_e=init_e,
        init_i=init_i,
        init_r=init_r,
        n_runs=n_runs,
        rng_seed=7,
        progress_callback=cb,
    )
    assert out["n_runs"] == n_runs
    assert progress_log, "progress_callback should fire"
    assert progress_log[-1]["runs_completed"] == n_runs
    assert progress_log[-1]["total_runs"] == n_runs
    assert out["patches"][0]["code"] == sched.patch_ids[0]
    n_days = sched.n_days
    for patch in out["patches"]:
        med = patch["i_median_by_day"]
        lo = patch["i_p2_5_by_day"]
        hi = patch["i_p97_5_by_day"]
        assert len(med) == n_days + 1
        assert len(lo) == n_days + 1
        assert len(hi) == n_days + 1
        for t in range(n_days + 1):
            assert lo[t] <= med[t] + 1e-9
            assert med[t] <= hi[t] + 1e-9
