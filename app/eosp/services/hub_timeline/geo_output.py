"""Build ``metadata.geo_forecast`` for dashboard heatmaps (ABM-compatible envelope)."""

from __future__ import annotations

import numpy as np

from eosp.services.hub_timeline.kernel import HubTrajectorySnapshot


def _pct_block(samples: np.ndarray) -> dict[str, float]:
    s = np.asarray(samples, dtype=float)
    return {
        "median": float(np.median(s)),
        "p2_5": float(np.percentile(s, 2.5)),
        "p25": float(np.percentile(s, 25.0)),
        "p75": float(np.percentile(s, 75.0)),
        "p97_5": float(np.percentile(s, 97.5)),
        "ci_95_lower": float(np.percentile(s, 2.5)),
        "ci_95_upper": float(np.percentile(s, 97.5)),
    }


def _bucket_code(iata: str, iata_to_country: dict[str, str]) -> str:
    cc = str(iata_to_country.get(iata, "??")).upper()
    if len(cc) != 2:
        cc = "??"
    return f"{cc}_{iata.upper()}"


def build_geo_forecast_dict(
    trajs: list[HubTrajectorySnapshot],
    *,
    iata_to_country: dict[str, str],
) -> dict:
    """Return ``{ "metadata": {...}, "by_day": [...] }`` matching ``tests/test_geo_abm_heatmap.py``."""

    if not trajs:
        return {"metadata": {"schema_version": "eosp_hub_geo_1"}, "by_day": []}
    n_days = len(trajs[0].dates)
    hubs = trajs[0].hubs
    n_tr = len(trajs)
    stack_cum = np.stack([t.cumulative_infected for t in trajs], axis=0)
    stack_a = np.stack([t.infectious_A for t in trajs], axis=0)
    stack_peak = np.stack([t.peak_unweighted_PI for t in trajs], axis=0)

    by_day: list[dict] = []
    for di in range(n_days):
        d = trajs[0].dates[di]
        buckets: dict[str, dict] = {}
        for hi, h in enumerate(hubs):
            code = _bucket_code(h, iata_to_country)
            c_med = stack_cum[:, di, hi]
            a_med = stack_a[:, di, hi]
            buckets[code] = {
                "cumulative_infected": _pct_block(c_med),
                "infectious_I": _pct_block(a_med),
            }
            if di == n_days - 1:
                buckets[code]["peak_infectious_I"] = _pct_block(stack_peak[:, hi])
        by_day.append({
            "day": di,
            "date": d.isoformat(),
            "buckets": buckets,
        })

    return {
        "metadata": {
            "schema_version": "eosp_hub_geo_1",
            "n_trajectories": n_tr,
            "n_days": n_days,
        },
        "by_day": by_day,
    }
