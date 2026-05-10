"""Sparse per-day / per-airport replay payloads for timeline UI."""

from __future__ import annotations

import numpy as np

from eosp.services.hub_timeline.kernel import HubTrajectorySnapshot


def build_replay_geo(
    trajs: list[HubTrajectorySnapshot],
    *,
    anchor_date_str: str,
    eps: float = 1e-12,
) -> dict:
    """Median surfaces A (infectious pressure) and C (cumulative stock); omit near-zero pairs."""

    if not trajs:
        return {
            "schema_version": "eosp_geo_replay_1",
            "anchor_date": anchor_date_str,
            "days": [],
        }

    n_days = len(trajs[0].dates)
    hubs = trajs[0].hubs
    stack_cum = np.stack([t.cumulative_infected for t in trajs], axis=0)
    stack_a = np.stack([t.infectious_A for t in trajs], axis=0)

    days_out: list[dict] = []
    for di, d in enumerate(trajs[0].dates):
        airports: dict[str, dict[str, float]] = {}
        for hi, code in enumerate(hubs):
            a_m = float(np.median(stack_a[:, di, hi]))
            c_m = float(np.median(stack_cum[:, di, hi]))
            if abs(a_m) <= eps and abs(c_m) <= eps:
                continue
            block: dict[str, float] = {}
            if abs(a_m) > eps:
                block["A_median"] = a_m
            if abs(c_m) > eps:
                block["C_median"] = c_m
            airports[code] = block
        if airports:
            days_out.append({"date": d.isoformat(), "airports": airports})

    return {
        "schema_version": "eosp_geo_replay_1",
        "anchor_date": anchor_date_str,
        "days": days_out,
    }
