from datetime import date

import numpy as np

from eosp.services.abm import Trajectory
from eosp.services.ensemble import _aggregate_geo_forecast


def test_aggregate_geo_forecast_nested_cumulative_and_infectious_I():
    labels = ("ship", "ZA_JNB")
    z = np.zeros
    t0 = Trajectory(
        n_days=2,
        daily_counts=z((3, 5), dtype=np.int32),
        cumulative_cases=z(3, dtype=np.int32),
        cumulative_deaths=z(3, dtype=np.int32),
        new_cases_per_day=z(3, dtype=np.int32),
        geo_bucket_labels=labels,
        bucket_cumulative_infected=np.array([[0, 0], [2, 0], [2, 1]], dtype=np.int32),
        bucket_infectious_I=np.array([[0, 0], [1, 0], [0, 1]], dtype=np.int32),
    )
    t1 = Trajectory(
        n_days=2,
        daily_counts=z((3, 5), dtype=np.int32),
        cumulative_cases=z(3, dtype=np.int32),
        cumulative_deaths=z(3, dtype=np.int32),
        new_cases_per_day=z(3, dtype=np.int32),
        geo_bucket_labels=labels,
        bucket_cumulative_infected=np.array([[0, 0], [4, 0], [4, 2]], dtype=np.int32),
        bucket_infectious_I=np.array([[0, 0], [2, 0], [1, 0]], dtype=np.int32),
    )
    out = _aggregate_geo_forecast(
        trajectories=[t0, t1],
        start_date=date(2026, 5, 7),
        n_days=2,
    )
    assert out is not None
    assert "metrics" in out and len(out["metrics"]) == 3
    day1 = out["by_day"][0]
    ship = day1["buckets"]["ship"]
    assert "cumulative_infected" in ship and "infectious_I" in ship
    assert "peak_infectious_I" not in ship
    assert ship["cumulative_infected"]["median"] == 3.0  # mean of 2 and 4
    assert ship["infectious_I"]["median"] == 1.5  # mean of 1 and 2
    last = out["by_day"][-1]["buckets"]["ship"]
    assert "peak_infectious_I" in last
    assert last["peak_infectious_I"]["median"] == 1.5
