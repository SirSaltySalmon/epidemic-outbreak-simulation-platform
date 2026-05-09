from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from eosp.services.patch_codes import iata_from_destination


@dataclass(frozen=True)
class PlaceGraph:
    """Canonical map nodes for P1 (SHIP + medevac airport IATAs)."""

    node_ids: tuple[str, ...]


def build_place_graph_from_network_spec(spec: dict[str, Any]) -> PlaceGraph:
    codes: set[str] = {"SHIP"}
    for flight in spec.get("flights", []):
        dest = flight.get("destination")
        if dest:
            codes.add(iata_from_destination(str(dest)))
    for dest_entry in spec.get("destinations", []):
        dest = dest_entry.get("destination")
        if dest:
            codes.add(iata_from_destination(str(dest)))
    ordered = ("SHIP",) + tuple(sorted(codes - {"SHIP"}))
    return PlaceGraph(node_ids=ordered)
