from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from eosp.services.patch_codes import iata_from_destination


@dataclass(frozen=True)
class PlaceGraph:
    """Canonical map nodes (hub / ledger IATAs for the contact cohort)."""

    node_ids: tuple[str, ...]


def build_place_graph_from_baseline(baseline: dict[str, Any]) -> PlaceGraph:
    codes: set[str] = set()
    for flight in baseline.get("flights") or []:
        dest = flight.get("destination_code") or flight.get("destination")
        if dest:
            codes.add(iata_from_destination(str(dest)))
        origin = str(flight.get("origin_iata") or "").strip().upper()
        if len(origin) == 3 and origin.isalpha():
            codes.add(origin)
    ordered = tuple(sorted(codes))
    return PlaceGraph(node_ids=ordered)


def build_place_graph_from_network_spec(spec: dict[str, Any]) -> PlaceGraph:
    """Accept legacy ``network_spec``-shaped dicts (tests / imports)."""

    return build_place_graph_from_baseline({"flights": spec.get("flights", [])})
