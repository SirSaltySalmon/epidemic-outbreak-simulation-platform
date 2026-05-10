"""OpenFlights route weights and non-local home priors for synthetic agents.

``sample_home_airport`` implements the design choice that with probability ``1 - p_home_here``
the agent's **home** hub is drawn from **global destination frequencies** pooled across all
edges (not conditional on the current airport). Duplicate ``routes.dat`` rows increase mass.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from eosp.services.openflights_routes import (
    all_iatas_from_counts,
    load_iata_to_country,
    outbound_weights_from_counts,
    parse_route_edge_counts,
)


def build_world_graph(
    routes_path: Path,
    airports_path: Path,
) -> tuple[frozenset[str], dict[str, dict[str, int]], dict[str, str], Counter[str]]:
    """Return ``allowed_iatas``, ``outbound`` adjacency, ``iata_to_country``, ``dest_weights``."""

    edge_counts = parse_route_edge_counts(routes_path)
    outbound = outbound_weights_from_counts(edge_counts)
    iata_to_country = load_iata_to_country(airports_path)
    route_codes = all_iatas_from_counts(edge_counts)
    allowed = frozenset(i for i in route_codes if len(i) == 3 and i.isalpha())
    dest_weights: Counter[str] = Counter()
    for (_, b), w in edge_counts.items():
        if b in allowed:
            dest_weights[b] += int(w)
    return allowed, outbound, iata_to_country, dest_weights


def sample_home_airport(
    rng: Any,
    current_iata: str,
    *,
    dest_weights: Counter[str],
    allowed: frozenset[str],
    p_home_here: float = 0.25,
) -> str:
    cur = str(current_iata).upper()
    if rng.random() < p_home_here or not dest_weights:
        return cur if cur in allowed else next(iter(allowed))
    codes = sorted(dest_weights.keys())
    w = [float(dest_weights[c]) for c in codes]
    import numpy as np

    arr = np.array(w, dtype=float)
    p = arr / arr.sum()
    pick = int(rng.choice(len(codes), p=p))
    return str(codes[pick])
