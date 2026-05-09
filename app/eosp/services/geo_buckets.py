"""Geographic / epidemiological buckets for ABM map overlays (FR-3 export).

**Buckets**
  - ``ship``: agents with ``node_metadata["ship_member"] is True`` (passengers + crew).
  - One bucket per distinct ``destination`` code on off-ship agents (e.g. ``ZA_JNB``,
    ``NL_AMS``), aligned with ``network_spec.json`` destination clusters.

**Map metric (cumulative infected in bucket)**  
  Per simulation day, count agents in the bucket whose SEIRD state is one of
  **E, I, R, or D** (i.e. not susceptible). This matches fleet-level
  ``cumulative_cases`` semantics scoped to the bucket.

**Not covered**  
  Second-hop airports and global rings use :mod:`eosp.services.risk_propagation`
  (OpenSky heuristic), not particle positions in the ABM graph.
"""

from __future__ import annotations

import numpy as np

from eosp.services.network import ContactNetwork

GEO_BUCKET_METRIC_ID = "cumulative_infected"
GEO_BUCKET_METRIC_DETAIL = (
    "Agents in the bucket in states E, I, R, or D (everyone no longer susceptible)."
)

GEO_BUCKET_METRIC_INFECTIOUS_I_ID = "infectious_present"
GEO_BUCKET_METRIC_INFECTIOUS_I_DETAIL = (
    "Agents in the bucket in state I (infectious) only."
)

GEO_BUCKET_METRIC_PEAK_INFECTIOUS_I_ID = "peak_infectious_I"
GEO_BUCKET_METRIC_PEAK_INFECTIOUS_I_DETAIL = (
    "Peak concurrent infectious (I) count in the bucket on the simulation day with maximum total I."
)


def geo_bucket_spec(network: ContactNetwork) -> tuple[tuple[str, ...], np.ndarray]:
    """Return ``(labels, agent_bucket)`` with ``agent_bucket[i]`` in ``0..len(labels)-1``."""

    metas = network.node_metadata
    n_agents = len(metas)
    dest_codes = sorted(
        {str(m["destination"]) for m in metas if m.get("destination")},
    )
    labels = ("ship", *dest_codes)
    name_to_i = {name: i for i, name in enumerate(labels)}
    agent_bucket = np.zeros(n_agents, dtype=np.int32)
    for i, m in enumerate(metas):
        if m.get("ship_member"):
            agent_bucket[i] = name_to_i["ship"]
        elif m.get("destination"):
            agent_bucket[i] = name_to_i[str(m["destination"])]
        else:
            agent_bucket[i] = name_to_i["ship"]
    if n_agents == 0:
        return tuple(), np.zeros(0, dtype=np.int32)
    return labels, agent_bucket
