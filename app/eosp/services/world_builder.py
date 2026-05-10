"""Case- and baseline-driven hub contact world (Track B).

Builds :class:`eosp.services.network.ContactNetwork` for a **small airport contact cohort**
around observed hub cases: **no ship** strata, **no** edge layers (patch / in-flight
mass action only). Onward geography is a Markov chain on OpenFlights ``routes.dat``
with optional **domestic continuation bias** from :func:`sample_next_airport`.

Cohort size comes from ``flight_schedules_baseline.json`` ``n_passengers`` per hub row
—not from any ``spawn_profile.ship`` counts (that block is UI / narrative only).

.. note::
   :class:`~eosp.core.models.InferenceResult` drives **transmission parameters** only.
   Initial E/I/R/D placement uses case-derived **aggregate counts** allocated on this
   cohort (see :func:`~eosp.services.ensemble.seed_state_from_case_records`), including
   proportional scaling to the pool when raw totals exceed ``N_pool``.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from eosp.core.models import CaseRecord
from eosp.services.case_seed import seed_manifest_from_cases
from eosp.services.network import ContactNetwork, gateway_weights_from_network_spec, iata_from_destination
from eosp.services.openflights_routes import (
    all_iatas_from_counts,
    load_iata_to_country,
    outbound_weights_from_counts,
    parse_route_edge_counts,
    sample_next_airport,
)
from eosp.services.schedule_baseline import compute_snapshot_id, load_baseline_schedule

_SPAWN_PATH = Path(__file__).resolve().parent.parent / "data" / "spawn_profile.json"
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _default_spawn_path() -> Path:
    return _SPAWN_PATH


def load_spawn_profile(path: Path | str | None = None) -> dict[str, Any]:
    p = Path(path) if path else _default_spawn_path()
    return json.loads(p.read_text(encoding="utf-8"))


def _default_routes_path() -> Path:
    env = os.environ.get("EOSP_OPENFLIGHTS_ROUTES")
    if env:
        return Path(env)
    return _REPO_ROOT / "data" / "raw" / "openflights" / "routes.dat"


def _default_airports_path() -> Path:
    env = os.environ.get("EOSP_OPENFLIGHTS_AIRPORTS")
    if env:
        return Path(env)
    return _REPO_ROOT / "data" / "raw" / "openflights" / "airports.dat"


def _normalize_flights_for_gateway(flights: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for f in flights:
        row = dict(f)
        row.setdefault("name", str(row.get("key", "flight")))
        dest = row.get("destination") or row.get("destination_code")
        if dest is not None:
            row["destination"] = dest
        out.append(row)
    return out


def build_world_contact_network(
    *,
    cases: list[CaseRecord] | None = None,
    n_days: int = 14,
    spawn: dict[str, Any] | None = None,
    baseline: dict[str, Any] | None = None,
    spawn_path: Path | str | None = None,
    baseline_path: Path | str | None = None,
    routes_path: Path | str | None = None,
    airports_path: Path | str | None = None,
    rng: Any | None = None,
) -> ContactNetwork:
    import numpy as np

    cases = cases or []
    spawn = spawn if spawn is not None else load_spawn_profile(spawn_path)
    baseline = baseline if baseline is not None else load_baseline_schedule(baseline_path)
    rng = rng or np.random.default_rng(int(spawn.get("rng_seed", 20260507)))

    rp = Path(routes_path) if routes_path else _default_routes_path()
    ap = Path(airports_path) if airports_path else _default_airports_path()

    routes_sha: str | None = None
    outbound: dict[str, dict[str, int]] = {}
    route_codes: set[str] = set()
    if rp.is_file():
        routes_sha = hashlib.sha256(rp.read_bytes()).hexdigest()
        ec = parse_route_edge_counts(rp)
        outbound = outbound_weights_from_counts(ec)
        route_codes = all_iatas_from_counts(ec)

    iata_to_country = load_iata_to_country(ap) if ap.is_file() else {}
    domestic_bias = float(spawn.get("domestic_continuation_bias", 0.4))

    flights_raw = list(baseline.get("flights") or [])
    flights_norm = _normalize_flights_for_gateway(flights_raw)

    baseline_for_snap = dict(baseline)
    if routes_sha:
        baseline_for_snap["openflights_routes_sha256"] = routes_sha
    snapshot_id = compute_snapshot_id(baseline_for_snap)

    gw_eps = float(spawn.get("gateway_pseudocount", 0.5))
    gw_allow = spawn.get("gateway_allowlist")
    allow_iter: list[str] | None = gw_allow if isinstance(gw_allow, list) else None
    gateway_weights = gateway_weights_from_network_spec(
        {"flights": flights_norm}, allowlist=allow_iter, pseudocount=gw_eps
    )

    metadata: list[dict[str, Any]] = []
    hubs_set: set[str] = set()
    for fc in flights_norm:
        dest_full = str(fc.get("destination") or fc.get("destination_code", ""))
        hub_iata = iata_from_destination(dest_full)
        if not hub_iata:
            continue
        hubs_set.add(hub_iata)
        n_here = int(fc.get("n_passengers", 0))
        for offset in range(n_here):
            metadata.append(
                {
                    "id": f"{hub_iata}_contact_{offset:03d}",
                    "role": "airport_contact",
                    "cohort_agent": True,
                    "destination": dest_full,
                    "home_iata": hub_iata,
                }
            )

    labels_tuple = tuple(sorted(route_codes | hubs_set)) if route_codes else tuple(sorted(hubs_set))
    label_to_i = {lab: i for i, lab in enumerate(labels_tuple)}

    n_agents = len(metadata)
    patch = np.zeros((n_agents, n_days), dtype=np.int32)
    flight_g = np.zeros((n_agents, n_days), dtype=np.int32)
    agent_legs: dict[int, list[tuple[int, str, str]]] = {}

    for agent_idx, meta in enumerate(metadata):
        hub = str(meta["home_iata"]).upper()
        patch[agent_idx, 0] = label_to_i[hub]
        cur = hub
        for day in range(1, n_days):
            nxt = sample_next_airport(
                rng,
                outbound,
                cur,
                iata_to_country=iata_to_country,
                domestic_bias=domestic_bias,
            )
            patch[agent_idx, day] = label_to_i[nxt]
            if nxt != cur:
                legs = agent_legs.setdefault(agent_idx, [])
                legs.append((day, cur, nxt))
            cur = nxt

    manifest = seed_manifest_from_cases(cases)
    return ContactNetwork(
        n_agents=n_agents,
        layers=[],
        node_metadata=metadata,
        n_days=n_days,
        gateway_weights=gateway_weights,
        gateway_pseudocount=gw_eps,
        flight_passengers_by_flight=None,
        itinerary_contact_patch=patch,
        itinerary_flight_group=flight_g,
        itinerary_bucket_labels=labels_tuple,
        itinerary_snapshot_id=snapshot_id,
        itinerary_seed_manifest=manifest,
        itinerary_patch_weight=1.0,
        itinerary_flight_weight=1.0,
        agent_itinerary_legs=agent_legs or None,
    )
