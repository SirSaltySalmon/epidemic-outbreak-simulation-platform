"""Three-ring global risk propagation from the MV Hondius outbreak.

Ring 0: The ship itself.
Ring 1: Confirmed evacuation flight destinations (JNB, AMS, TFN).
Ring 2: All airports reachable by departing flights from Ring 1 airports
        within the 8-day Andes hantavirus incubation window.
Ring 3: Second-hop airports from the busiest Ring 2 airports (optional,
        included only when risk_score > 0.05 to limit noise).

Risk scores decay with each hop and are proportional to the number of
exposed passengers and the inferred transmission probability.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from eosp.services.opensky import get_departures, load_airport_coords

logger = logging.getLogger(__name__)

_COORDS = None  # lazy-loaded

_INCUBATION_DAYS = 8
_RING2_DECAY = 0.30
_RING3_DECAY = 0.10
_RING3_THRESHOLD = 0.05

# Unix timestamps for the evacuation flights (approximate)
# JNB flight ~April 25, AMS flight ~May 3, TFN flight ~May 5
_EVACUATION_EVENTS = [
    {"airport_iata": "JNB", "depart_day_offset": 0,  "passengers": 30, "ts": 1745539200},
    {"airport_iata": "AMS", "depart_day_offset": 8,  "passengers": 40, "ts": 1746230400},
    {"airport_iata": "TFN", "depart_day_offset": 10, "passengers": 52, "ts": 1746403200},
]
_TOTAL_EVACUEES = sum(e["passengers"] for e in _EVACUATION_EVENTS)


@dataclass
class RiskZone:
    airport_iata: str
    lat: float
    lng: float
    city: str
    country: str
    risk_score: float
    ring: int


def _coords() -> dict:
    global _COORDS
    if _COORDS is None:
        _COORDS = load_airport_coords()
    return _COORDS


def compute_risk_zones(p_transmit: float) -> list[RiskZone]:
    """Build the full risk zone list for the geo/outbreak endpoint.

    ``p_transmit`` comes from the latest inference posterior mean.
    """
    coords = _coords()
    zones: dict[str, RiskZone] = {}

    # Ring 1 — direct evacuation destinations
    for evt in _EVACUATION_EVENTS:
        iata = evt["airport_iata"]
        if iata not in coords:
            continue
        c = coords[iata]
        ring1_score = min(1.0, (evt["passengers"] / _TOTAL_EVACUEES) * p_transmit * 12.0)
        zones[iata] = RiskZone(
            airport_iata=iata, lat=c["lat"], lng=c["lng"],
            city=c["city"], country=c["country"],
            risk_score=ring1_score, ring=1,
        )

    # Ring 2 — onward flights from Ring 1 airports within incubation window
    ring1_airports = list(zones.keys())
    ring2_raw: dict[str, float] = {}
    for r1_iata in ring1_airports:
        r1_score = zones[r1_iata].risk_score
        evt = next(e for e in _EVACUATION_EVENTS if e["airport_iata"] == r1_iata)
        begin_ts = evt["ts"]
        end_ts = begin_ts + _INCUBATION_DAYS * 86400
        flights = get_departures(r1_iata, begin_ts, end_ts)
        for flight in flights:
            dest = flight.dest_airport_iata
            if dest is None or dest == r1_iata or dest not in coords:
                continue
            hop_score = r1_score * _RING2_DECAY * p_transmit * 3.0
            ring2_raw[dest] = max(ring2_raw.get(dest, 0.0), hop_score)

    for iata, score in ring2_raw.items():
        if iata in zones:
            continue
        c = coords[iata]
        zones[iata] = RiskZone(
            airport_iata=iata, lat=c["lat"], lng=c["lng"],
            city=c["city"], country=c["country"],
            risk_score=round(min(score, 0.95), 4), ring=2,
        )

    # Ring 3 — second hop from top-5 Ring 2 airports
    top_ring2 = sorted(
        [(iata, z) for iata, z in zones.items() if z.ring == 2],
        key=lambda x: x[1].risk_score, reverse=True,
    )[:5]
    ring3_raw: dict[str, float] = {}
    for r2_iata, r2_zone in top_ring2:
        evt_ts = int(time.time()) - 7 * 86400  # approximate recent window
        end_ts = evt_ts + _INCUBATION_DAYS * 86400
        flights = get_departures(r2_iata, evt_ts, end_ts)
        for flight in flights:
            dest = flight.dest_airport_iata
            if dest is None or dest in zones or dest not in coords:
                continue
            hop_score = r2_zone.risk_score * _RING3_DECAY * p_transmit * 2.0
            ring3_raw[dest] = max(ring3_raw.get(dest, 0.0), hop_score)

    for iata, score in ring3_raw.items():
        if score < _RING3_THRESHOLD:
            continue
        c = coords[iata]
        zones[iata] = RiskZone(
            airport_iata=iata, lat=c["lat"], lng=c["lng"],
            city=c["city"], country=c["country"],
            risk_score=round(min(score, 0.95), 4), ring=3,
        )

    return sorted(zones.values(), key=lambda z: (-z.ring, -z.risk_score))
