"""Contact network construction for EOSP (FR-3.1).

Builds a layered, temporally-aware contact graph. The default **hub contact-cohort**
path (:func:`build_default_network` without a spec) uses empty edge layers and
itinerary-driven patch mixing. Passing a legacy ``spec`` dict still builds the
older ship-and-destination graph for tests and migrations.
"""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import networkx as nx
import numpy as np
from scipy import sparse


EDGE_TYPES = ("household", "workplace", "social", "transient", "flight", "hospital", "family")


@dataclass
class EdgeLayer:
    """One temporal layer of weighted edges sharing a contact type."""

    name: str
    type: str
    edges: list[tuple[int, int, float]]
    active_from: int
    active_to: int


@dataclass
class ContactNetwork:
    n_agents: int
    layers: list[EdgeLayer]
    node_metadata: list[dict[str, Any]]
    n_days: int = 14
    gateway_weights: dict[str, float] | None = None
    gateway_pseudocount: float | None = None
    flight_passengers_by_flight: dict[str, list[int]] | None = None
    itinerary_contact_patch: np.ndarray | None = None
    itinerary_flight_group: np.ndarray | None = None
    itinerary_bucket_labels: tuple[str, ...] | None = None
    itinerary_snapshot_id: str | None = None
    itinerary_seed_manifest: dict[str, Any] | None = None
    itinerary_patch_weight: float = 1.0
    itinerary_flight_weight: float = 1.0
    agent_itinerary_legs: dict[int, list[tuple[int, str, str]]] | None = None

    def graph(self) -> nx.DiGraph:
        graph = nx.DiGraph()
        for index, meta in enumerate(self.node_metadata):
            graph.add_node(index, **meta)
        for layer in self.layers:
            for source, target, weight in layer.edges:
                graph.add_edge(
                    source,
                    target,
                    type=layer.type,
                    weight=weight,
                    active_from=layer.active_from,
                    active_to=layer.active_to,
                )
        return graph

    def adjacency_for_day(self, day: int) -> sparse.csr_matrix:
        rows: list[int] = []
        cols: list[int] = []
        data: list[float] = []
        for layer in self.layers:
            if not (layer.active_from <= day <= layer.active_to):
                continue
            for source, target, weight in layer.edges:
                rows.append(source)
                cols.append(target)
                data.append(weight)
                rows.append(target)
                cols.append(source)
                data.append(weight)
        if not rows:
            return sparse.csr_matrix((self.n_agents, self.n_agents))
        coo = sparse.coo_matrix((data, (rows, cols)), shape=(self.n_agents, self.n_agents))
        return coo.tocsr()

    def ship_node_indices(self) -> list[int]:
        return [index for index, meta in enumerate(self.node_metadata) if meta.get("ship_member")]

    def degree_summary(self) -> dict[str, float]:
        """Aggregate weighted degree per agent averaged across the simulation horizon.

        Used by the Bayesian next-generation operator (FR-2.1) to translate
        ``(p_transmit, contacts_daily, h2h_multiplier)`` into expected daily
        onsets without running the stochastic ABM inside MCMC.
        """

        if self.itinerary_contact_patch is not None:
            return self._itinerary_degree_summary()

        n_days = max(1, self.n_days)
        degree = np.zeros(self.n_agents, dtype=float)
        for layer in self.layers:
            duration = max(0, min(layer.active_to, n_days - 1) - max(layer.active_from, 0) + 1)
            if duration <= 0:
                continue
            for source, target, weight in layer.edges:
                degree[source] += weight * duration
                degree[target] += weight * duration
        per_day = degree / n_days
        return {
            "mean_weighted_degree": float(per_day.mean()),
            "max_weighted_degree": float(per_day.max()),
            "ship_population": float(sum(1 for meta in self.node_metadata if meta.get("ship_member"))),
            "n_agents": float(self.n_agents),
        }

    def _itinerary_degree_summary(self) -> dict[str, float]:
        """Approximate mean weighted degree from patch + in-flight co-location (Track B)."""

        arr = self.itinerary_contact_patch
        fg = self.itinerary_flight_group
        if arr is None:
            return {
                "mean_weighted_degree": 0.0,
                "max_weighted_degree": 0.0,
                "ship_population": float(sum(1 for meta in self.node_metadata if meta.get("ship_member"))),
                "n_agents": float(self.n_agents),
            }
        n_days_eff = min(int(self.n_days), int(arr.shape[1]))
        pw = float(self.itinerary_patch_weight)
        fw = float(self.itinerary_flight_weight)
        degree = np.zeros(self.n_agents, dtype=float)
        for day in range(n_days_eff):
            patch_ids = arr[:, day]
            max_b = int(np.max(patch_ids)) + 1 if patch_ids.size else 1
            patch_pop = np.bincount(patch_ids.astype(np.int64, copy=False), minlength=max_b)
            for agent in range(self.n_agents):
                b = int(patch_ids[agent])
                n_b = int(patch_pop[b])
                if n_b > 1:
                    degree[agent] += pw * float(n_b - 1)
            if fg is not None and fg.shape[1] > day:
                gcol = fg[:, day]
                gpos = gcol[gcol > 0]
                if gpos.size:
                    max_g = int(gpos.max()) + 1
                    flight_pop = np.bincount(gcol.astype(np.int64, copy=False), minlength=max_g)
                    for agent in range(self.n_agents):
                        g = int(gcol[agent])
                        if g <= 0:
                            continue
                        n_g = int(flight_pop[g])
                        if n_g > 1:
                            degree[agent] += fw * float(n_g - 1)
        per_day = degree / max(1, n_days_eff)
        return {
            "mean_weighted_degree": float(per_day.mean()),
            "max_weighted_degree": float(per_day.max()),
            "ship_population": float(sum(1 for meta in self.node_metadata if meta.get("ship_member"))),
            "n_agents": float(self.n_agents),
        }


def load_spec(path: Path | str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def iata_from_destination(destination_code: str) -> str:
    """Map a spec destination key (e.g. ``ZA_JNB``) to a 3-letter IATA code."""

    parts = str(destination_code).strip().upper().split("_")
    if len(parts) >= 2 and len(parts[-1]) == 3 and parts[-1].isalpha():
        return parts[-1]
    code = str(destination_code).strip().upper()
    return code[-3:] if len(code) >= 3 else code


def gateway_weights_from_network_spec(
    spec: dict[str, Any],
    *,
    allowlist: Iterable[str] | None = None,
    pseudocount: float = 0.5,
) -> dict[str, float]:
    """Evacuation movement prior from ``network_spec`` flight passenger counts (§4.2).

    Applies Laplace smoothing per gateway in ``allowlist`` (or all observed gateways
    when ``allowlist`` is None). Metadata should record ``gateway_pseudocount``.
    """

    counts: dict[str, int] = {}
    for flight in spec.get("flights", []):
        dest = flight.get("destination")
        if dest is None:
            continue
        iata = iata_from_destination(str(dest))
        counts[iata] = counts.get(iata, 0) + int(flight.get("n_passengers", 0))
    if allowlist is None:
        keys: list[str] = sorted(counts.keys())
    else:
        keys = list(dict.fromkeys(str(x).upper() for x in allowlist))
    if not keys:
        return {}
    eps = float(pseudocount)
    weighted = {k: float(counts.get(k, 0)) + eps for k in keys}
    total = sum(weighted.values())
    if total <= 0:
        total = 1.0
    return {k: weighted[k] / total for k in keys}


def assign_flight_passengers_from_spec(
    rng: np.random.Generator,
    n_passengers: int,
    n_crew: int,
    flights: list[dict[str, Any]],
) -> dict[str, list[int]]:
    """Assign each ship agent to at most one evacuation flight (capacity = sum of ``n_passengers``)."""

    n_ship = n_passengers + n_crew
    slots: list[dict[str, Any]] = []
    for flight_cfg in flights:
        for _ in range(int(flight_cfg["n_passengers"])):
            slots.append(flight_cfg)
    rng.shuffle(slots)
    agents = np.arange(n_ship, dtype=np.int64)
    rng.shuffle(agents)
    by_name: dict[str, list[int]] = {}
    for flight_cfg in flights:
        nm = str(flight_cfg.get("name") or flight_cfg.get("key", ""))
        by_name[nm] = []
    for slot_index, flight_cfg in enumerate(slots):
        if slot_index >= n_ship:
            break
        agent = int(agents[slot_index])
        nm = str(flight_cfg.get("name") or flight_cfg.get("key", ""))
        by_name[nm].append(agent)
    for key in by_name:
        by_name[key].sort()
    return by_name


def build_default_network(
    spec: dict[str, Any] | None = None,
    n_days: int = 14,
    cases: Any | None = None,
) -> ContactNetwork:
    if spec is not None:
        return _legacy_contact_network_from_spec(spec, n_days=n_days)
    from eosp.services.world_builder import build_world_contact_network

    return build_world_contact_network(cases=cases or [], n_days=n_days)


def _legacy_contact_network_from_spec(spec: dict[str, Any], *, n_days: int) -> ContactNetwork:
    rng = np.random.default_rng(spec.get("rng_seed", 20260507))
    gw_eps = float(spec.get("gateway_pseudocount", 0.5))
    gw_allow = spec.get("gateway_allowlist")
    allow_iter: Iterable[str] | None = gw_allow if isinstance(gw_allow, list) else None
    gateway_weights = gateway_weights_from_network_spec(spec, allowlist=allow_iter, pseudocount=gw_eps)

    ship_cfg = spec["ship"]
    n_passengers = int(ship_cfg["n_passengers"])
    n_crew = int(ship_cfg["n_crew"])
    n_ship = n_passengers + n_crew
    edge_weights = spec["edge_weights"]
    ship_window = ship_cfg["active_window"]
    ship_active_to = min(int(ship_window["end_day"]), n_days - 1)

    metadata: list[dict[str, Any]] = []
    for index in range(n_passengers):
        metadata.append(
            {
                "id": f"ship_passenger_{index:03d}",
                "role": "passenger",
                "ship_member": True,
                "destination": None,
            }
        )
    for index in range(n_crew):
        metadata.append(
            {
                "id": f"ship_crew_{index:03d}",
                "role": "crew",
                "ship_member": True,
                "destination": None,
            }
        )

    layers: list[EdgeLayer] = []

    layers.append(_build_household_layer(rng, n_passengers, ship_cfg["cabins"], edge_weights["household"], ship_active_to))
    layers.append(_build_workplace_layer(n_passengers, n_crew, ship_cfg["crew_workgroups"], edge_weights["workplace"], ship_active_to))
    layers.append(_build_social_layer(rng, n_ship, ship_cfg["social"]["mean_social_contacts"], edge_weights["social"], ship_active_to))
    layers.append(_build_transient_layer(rng, n_ship, ship_cfg["social"]["mean_transient_contacts"], edge_weights["transient"], ship_active_to))

    next_node = n_ship
    
    def _norm_flights(flights: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for f in flights:
            row = dict(f)
            row.setdefault("name", str(row.get("key", "flight")))
            dest = row.get("destination") or row.get("destination_code")
            if dest is not None:
                row["destination"] = dest
            out.append(row)
        return out

    flights_cfg = _norm_flights(list(spec.get("flights", [])))
    flight_book = assign_flight_passengers_from_spec(rng, n_passengers, n_crew, flights_cfg)
    for flight_cfg in flights_cfg:
        flight_passengers = flight_book[str(flight_cfg["name"])]
        layers.append(_build_flight_layer(flight_cfg, flight_passengers, n_days))
        destination_cfg = next(
            (entry for entry in spec.get("destinations", []) if entry["destination"] == flight_cfg["destination"]),
            None,
        )
        if destination_cfg is not None:
            healthcare_indices = list(range(next_node, next_node + int(destination_cfg["n_healthcare_workers"])))
            for offset, index in enumerate(healthcare_indices):
                metadata.append(
                    {
                        "id": f"{destination_cfg['name']}_hcw_{offset:03d}",
                        "role": "healthcare_worker",
                        "ship_member": False,
                        "destination": destination_cfg["destination"],
                    }
                )
            next_node += len(healthcare_indices)
            family_indices = list(range(next_node, next_node + int(destination_cfg["n_family_contacts"])))
            for offset, index in enumerate(family_indices):
                metadata.append(
                    {
                        "id": f"{destination_cfg['name']}_family_{offset:03d}",
                        "role": "family_contact",
                        "ship_member": False,
                        "destination": destination_cfg["destination"],
                    }
                )
            next_node += len(family_indices)
            depart_day = int(flight_cfg["depart_day"])
            arrival_day = min(depart_day + 1, n_days - 1)
            layers.append(
                _build_destination_layer(
                    rng=rng,
                    name=f"{destination_cfg['name']}_hospital",
                    type_name="hospital",
                    flight_passengers=flight_passengers,
                    cluster_indices=healthcare_indices,
                    weight=edge_weights["hospital"],
                    active_from=arrival_day,
                    active_to=n_days - 1,
                    contacts_per_passenger=2,
                )
            )
            layers.append(
                _build_destination_layer(
                    rng=rng,
                    name=f"{destination_cfg['name']}_family",
                    type_name="family",
                    flight_passengers=flight_passengers,
                    cluster_indices=family_indices,
                    weight=edge_weights["family"],
                    active_from=arrival_day,
                    active_to=n_days - 1,
                    contacts_per_passenger=1,
                )
            )

    n_agents = len(metadata)
    return ContactNetwork(
        n_agents=n_agents,
        layers=layers,
        node_metadata=metadata,
        n_days=n_days,
        gateway_weights=gateway_weights,
        gateway_pseudocount=gw_eps,
        flight_passengers_by_flight=flight_book,
    )


def apply_modifications(network: ContactNetwork, modifications: dict[str, Any]) -> ContactNetwork:
    """Return a new ContactNetwork with the supplied scenario modifications applied.

    Supported keys (FR-3.3):
      - ``remove_edges``: list of edge type names to drop entirely
      - ``modify_weights``: mapping of edge type or ``"<layer-prefix>_*"`` glob to a multiplier
      - ``add_isolation``: list of windows ``"day_<from>_to_<to>"`` that mute every layer in range
    """

    remove_types = set(modifications.get("remove_edges") or [])
    weight_mods = modifications.get("modify_weights") or {}
    isolation_windows = _parse_isolation_windows(modifications.get("add_isolation") or [])

    pw = float(network.itinerary_patch_weight)
    fw = float(network.itinerary_flight_weight)
    if network.itinerary_contact_patch is not None:
        pw *= float(weight_mods.get("itinerary_patch", 1.0))
        fw *= float(weight_mods.get("itinerary_flight", 1.0))

    new_layers: list[EdgeLayer] = []
    for layer in network.layers:
        if layer.type in remove_types or layer.name in remove_types:
            continue
        scale = _resolve_weight_scale(layer, weight_mods)
        scaled_edges = [(u, v, w * scale) for u, v, w in layer.edges]
        adjusted = EdgeLayer(
            name=layer.name,
            type=layer.type,
            edges=scaled_edges,
            active_from=layer.active_from,
            active_to=layer.active_to,
        )
        adjusted = _apply_isolation_windows(adjusted, isolation_windows)
        if adjusted.edges:
            new_layers.append(adjusted)
    return ContactNetwork(
        n_agents=network.n_agents,
        layers=new_layers,
        node_metadata=list(network.node_metadata),
        n_days=network.n_days,
        gateway_weights=network.gateway_weights,
        gateway_pseudocount=network.gateway_pseudocount,
        flight_passengers_by_flight=network.flight_passengers_by_flight,
        itinerary_contact_patch=network.itinerary_contact_patch,
        itinerary_flight_group=network.itinerary_flight_group,
        itinerary_bucket_labels=network.itinerary_bucket_labels,
        itinerary_snapshot_id=network.itinerary_snapshot_id,
        itinerary_seed_manifest=network.itinerary_seed_manifest,
        itinerary_patch_weight=pw,
        itinerary_flight_weight=fw,
        agent_itinerary_legs=network.agent_itinerary_legs,
    )


def _build_household_layer(
    rng: np.random.Generator,
    n_passengers: int,
    cabin_cfg: dict[str, Any],
    weight: float,
    active_to: int,
) -> EdgeLayer:
    occupancy = max(1, int(cabin_cfg["occupancy"]))
    assignments = list(range(n_passengers))
    rng.shuffle(assignments)
    edges: list[tuple[int, int, float]] = []
    for chunk_start in range(0, len(assignments) - occupancy + 1, occupancy):
        members = assignments[chunk_start : chunk_start + occupancy]
        for source, target in itertools.combinations(members, 2):
            edges.append((source, target, weight))
    return EdgeLayer(name="ship_household", type="household", edges=edges, active_from=0, active_to=active_to)


def _build_workplace_layer(
    n_passengers: int,
    n_crew: int,
    workgroup_cfg: dict[str, Any],
    weight: float,
    active_to: int,
) -> EdgeLayer:
    edges: list[tuple[int, int, float]] = []
    members_per_group = max(1, int(workgroup_cfg["members_per_group"]))
    crew_indices = list(range(n_passengers, n_passengers + n_crew))
    for start in range(0, len(crew_indices) - members_per_group + 1, members_per_group):
        group = crew_indices[start : start + members_per_group]
        for source, target in itertools.combinations(group, 2):
            edges.append((source, target, weight))
    return EdgeLayer(name="ship_workplace", type="workplace", edges=edges, active_from=0, active_to=active_to)


def _build_social_layer(
    rng: np.random.Generator,
    n_ship: int,
    mean_social: int,
    weight: float,
    active_to: int,
) -> EdgeLayer:
    edges: list[tuple[int, int, float]] = []
    seen: set[tuple[int, int]] = set()
    target_count = max(1, int(mean_social * n_ship / 2))
    while len(edges) < target_count:
        source, target = rng.integers(0, n_ship, size=2)
        source, target = int(source), int(target)
        if source == target:
            continue
        key = (min(source, target), max(source, target))
        if key in seen:
            continue
        seen.add(key)
        edges.append((source, target, weight))
    return EdgeLayer(name="ship_social", type="social", edges=edges, active_from=0, active_to=active_to)


def _build_transient_layer(
    rng: np.random.Generator,
    n_ship: int,
    mean_transient: int,
    weight: float,
    active_to: int,
) -> EdgeLayer:
    edges: list[tuple[int, int, float]] = []
    seen: set[tuple[int, int]] = set()
    target_count = max(1, int(mean_transient * n_ship / 2))
    while len(edges) < target_count:
        source, target = rng.integers(0, n_ship, size=2)
        source, target = int(source), int(target)
        if source == target:
            continue
        key = (min(source, target), max(source, target))
        if key in seen:
            continue
        seen.add(key)
        edges.append((source, target, weight))
    return EdgeLayer(name="ship_transient", type="transient", edges=edges, active_from=0, active_to=active_to)


def _build_flight_layer(
    flight_cfg: dict[str, Any],
    flight_passengers: list[int],
    n_days: int,
) -> EdgeLayer:
    radius = max(1, int(flight_cfg.get("neighbour_radius", 2)))
    weight = float(flight_cfg.get("edge_weight", 0.55))
    edges: list[tuple[int, int, float]] = []
    for pos, source in enumerate(flight_passengers):
        for offset in range(1, radius + 1):
            if pos + offset < len(flight_passengers):
                target = flight_passengers[pos + offset]
                edges.append((source, target, weight * (1.0 - 0.15 * (offset - 1))))
    depart_day = min(int(flight_cfg["depart_day"]), n_days - 1)
    return EdgeLayer(
        name=str(flight_cfg["name"]),
        type="flight",
        edges=edges,
        active_from=depart_day,
        active_to=depart_day,
    )


def _build_destination_layer(
    rng: np.random.Generator,
    name: str,
    type_name: str,
    flight_passengers: Iterable[int],
    cluster_indices: list[int],
    weight: float,
    active_from: int,
    active_to: int,
    contacts_per_passenger: int,
) -> EdgeLayer:
    edges: list[tuple[int, int, float]] = []
    cluster = list(cluster_indices)
    if not cluster:
        return EdgeLayer(name=name, type=type_name, edges=[], active_from=active_from, active_to=active_to)
    for passenger in flight_passengers:
        targets = rng.choice(cluster, size=min(contacts_per_passenger, len(cluster)), replace=False)
        for target in np.atleast_1d(targets):
            edges.append((int(passenger), int(target), weight))
    return EdgeLayer(name=name, type=type_name, edges=edges, active_from=active_from, active_to=active_to)


def _resolve_weight_scale(layer: EdgeLayer, weight_mods: dict[str, float]) -> float:
    scale = 1.0
    for key, value in weight_mods.items():
        if key == layer.type:
            scale *= float(value)
        elif key.endswith("*"):
            prefix = key[:-1]
            if layer.name.startswith(prefix) or layer.type.startswith(prefix):
                scale *= float(value)
        elif key == layer.name:
            scale *= float(value)
    return scale


def _parse_isolation_windows(windows: list[str]) -> list[tuple[int, int]]:
    parsed: list[tuple[int, int]] = []
    for entry in windows:
        try:
            _, _, start, _, end = entry.split("_")
            parsed.append((int(start), int(end)))
        except ValueError:
            continue
    return parsed


def _apply_isolation_windows(layer: EdgeLayer, windows: list[tuple[int, int]]) -> EdgeLayer:
    if not windows:
        return layer
    keep = True
    for start, end in windows:
        if layer.active_from >= start and layer.active_to <= end and layer.type in {"social", "transient", "workplace"}:
            keep = False
            break
    if keep:
        return layer
    return EdgeLayer(name=layer.name, type=layer.type, edges=[], active_from=layer.active_from, active_to=layer.active_to)
