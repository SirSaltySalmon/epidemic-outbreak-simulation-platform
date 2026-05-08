"""Assembles the geo/outbreak response for the Leaflet world map.

Reads static network_spec.json for ship/flight topology,
enriches with airport coordinates, and calls risk_propagation to
build the global heatmap. Result is safe to cache for 12 hours.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from eosp.services.opensky import load_airport_coords
from eosp.services.risk_propagation import compute_risk_zones

_SPEC_PATH = Path(__file__).resolve().parent.parent / "data" / "network_spec.json"

# MV Hondius approximate position — en route Cape Verde → Tenerife
_SHIP_LAT = 20.5
_SHIP_LNG = -21.0

# Known case locations by country ISO code
_CASE_COORDS: dict[str, dict[str, Any]] = {
    "ZA": {"lat": -26.134, "lng": 28.242, "confirmed": 2, "suspected": 0, "deaths": 1, "airport": "JNB"},
    "NL": {"lat": 52.309, "lng": 4.764,  "confirmed": 1, "suspected": 1, "deaths": 1, "airport": "AMS"},
    "CH": {"lat": 46.238, "lng": 6.109,  "confirmed": 0, "suspected": 1, "deaths": 1, "airport": "GVA"},
    "ES": {"lat": 28.048, "lng": -16.572, "confirmed": 0, "suspected": 3, "deaths": 0, "airport": "TFN"},
}


def build_outbreak_geo(p_transmit: float = 0.089) -> dict[str, Any]:
    """Build the complete geo/outbreak payload.

    ``p_transmit`` is taken from the latest inference result so risk
    scores update as the model refits.
    """
    spec = _load_spec()
    coords = load_airport_coords()

    ship = {
        "lat": _SHIP_LAT,
        "lng": _SHIP_LNG,
        "name": spec.get("ship", {}).get("name", "MV Hondius"),
        "status": "en_route_tenerife",
    }

    confirmed_cases = []
    for country, info in _CASE_COORDS.items():
        confirmed_cases.append({
            "country": country,
            "lat": info["lat"],
            "lng": info["lng"],
            "confirmed": info["confirmed"],
            "suspected": info["suspected"],
            "deaths": info["deaths"],
            "airport": info["airport"],
        })

    # Build evacuation flight arcs from network_spec.json
    evacuation_flights = []
    for flight in spec.get("flights", []):
        dest_code = flight.get("destination", "")  # e.g. "ZA_JNB"
        iata = dest_code.split("_")[-1] if "_" in dest_code else dest_code
        dest_coords = coords.get(iata)
        if dest_coords is None:
            continue
        evacuation_flights.append({
            "name": flight.get("name", ""),
            "from_lat": _SHIP_LAT,
            "from_lng": _SHIP_LNG,
            "to_lat": dest_coords["lat"],
            "to_lng": dest_coords["lng"],
            "to_airport": iata,
            "depart_day": flight.get("depart_day", 0),
            "passengers": flight.get("n_passengers", 0),
        })

    risk_zones = compute_risk_zones(p_transmit)
    heatmap = [
        {
            "airport_iata": z.airport_iata,
            "lat": z.lat,
            "lng": z.lng,
            "city": z.city,
            "country": z.country,
            "risk_score": z.risk_score,
            "ring": z.ring,
        }
        for z in risk_zones
    ]

    return {
        "ship": ship,
        "confirmed_cases": confirmed_cases,
        "evacuation_flights": evacuation_flights,
        "risk_heatmap": heatmap,
        "metadata": {
            "p_transmit_used": round(p_transmit, 4),
            "ring1_airports": [z.airport_iata for z in risk_zones if z.ring == 1],
            "ring2_airports_found": sum(1 for z in risk_zones if z.ring == 2),
            "ring3_airports_found": sum(1 for z in risk_zones if z.ring == 3),
        },
    }


def _load_spec() -> dict[str, Any]:
    with _SPEC_PATH.open() as fh:
        return json.load(fh)
