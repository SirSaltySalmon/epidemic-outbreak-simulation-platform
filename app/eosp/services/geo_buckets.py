"""Geographic / epidemiological buckets for ABM map overlays (FR-3 export).

**Track B (itinerary)**  
  When :class:`~eosp.services.network.ContactNetwork` carries
  ``itinerary_bucket_labels`` + ``itinerary_contact_patch``, buckets are **patches**
  (e.g. hub IATAs) encountered in the hub–OpenFlights world.

**Legacy (metadata destination)**  
  Otherwise: ``ship`` plus one bucket per distinct ``destination`` on off-ship agents.

**Map metric (cumulative infected in bucket)**  
  Per simulation day, count agents whose epidemiological state is one of
  **E, P, I, H, R, or D** (not susceptible), scoped to the bucket for that day.
"""

from __future__ import annotations

from typing import Any

from eosp.services.network import ContactNetwork

GEO_BUCKET_METRIC_ID = "cumulative_infected"
GEO_BUCKET_METRIC_DETAIL = (
    "Agents in the bucket in states E, P, I, H, R, or D (everyone no longer susceptible)."
)

GEO_BUCKET_METRIC_INFECTIOUS_I_ID = "infectious_present"
GEO_BUCKET_METRIC_INFECTIOUS_I_DETAIL = (
    "Agents in the bucket in presymptomatic (P) or symptomatic infectious (I) states."
)

GEO_BUCKET_METRIC_PEAK_INFECTIOUS_I_ID = "peak_infectious_I"
GEO_BUCKET_METRIC_PEAK_INFECTIOUS_I_DETAIL = (
    "Peak concurrent P+I (transmitting) count in the bucket on the simulation day with maximum total P+I."
)


def _require_numpy_bundle():  # lazy: ensembles / ABM load NumPy here
    import numpy as np

    return np


def geo_bucket_spec(network: ContactNetwork) -> tuple[tuple[str, ...], Any]:
    """Return ``(labels, agent_bucket)`` with ``agent_bucket[i]`` in ``0..len(labels)-1``."""

    np = _require_numpy_bundle()

    if network.itinerary_bucket_labels and network.itinerary_contact_patch is not None:
        labels = network.itinerary_bucket_labels
        arr = network.itinerary_contact_patch
        col0 = arr[:, 0] if arr.shape[1] else np.zeros(len(network.node_metadata), dtype=np.int32)
        return labels, col0.astype(np.int32, copy=False)

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
