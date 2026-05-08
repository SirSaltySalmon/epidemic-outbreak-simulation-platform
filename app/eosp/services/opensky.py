"""OpenSky Network API client for outbound flight enumeration (FR integration).

Queries departure records for a given airport within a time window.
Results are cached in-memory for 12 hours to respect the free-tier
400 calls/day limit. Falls back to an empty list on API error so the
geo endpoint always returns something usable.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_COORDS_PATH = Path(__file__).resolve().parent.parent / "data" / "airport_coords.json"
_CACHE_TTL_SECONDS = 43200  # 12 hours


@dataclass
class FlightRecord:
    callsign: str
    dest_airport_icao: str | None
    dest_airport_iata: str | None
    est_departure_time: int  # unix timestamp
    est_arrival_time: int | None


@dataclass
class _CacheEntry:
    data: list[FlightRecord]
    fetched_at: float = field(default_factory=time.monotonic)

    def is_fresh(self) -> bool:
        return (time.monotonic() - self.fetched_at) < _CACHE_TTL_SECONDS


_CACHE: dict[tuple[str, int, int], _CacheEntry] = {}


def _utc_day_chunks(begin_unix: int, end_unix: int) -> list[tuple[int, int]]:
    """Split [begin_unix, end_unix] into intervals each contained in one UTC calendar day.

    OpenSky's departures-by-airport endpoint rejects queries spanning more than one UTC day.
    """
    if end_unix < begin_unix:
        return []
    chunks: list[tuple[int, int]] = []
    day = datetime.fromtimestamp(begin_unix, UTC).date()
    last_day = datetime.fromtimestamp(end_unix, UTC).date()
    while day <= last_day:
        day_start = datetime(day.year, day.month, day.day, tzinfo=UTC)
        day_end_excl = day_start + timedelta(days=1)
        lo = max(begin_unix, int(day_start.timestamp()))
        hi = min(end_unix, int(day_end_excl.timestamp()) - 1)
        if lo <= hi:
            chunks.append((lo, hi))
        day += timedelta(days=1)
    return chunks


def load_airport_coords() -> dict[str, dict[str, Any]]:
    with _COORDS_PATH.open() as fh:
        return json.load(fh)


def iata_to_icao(iata: str) -> str:
    """Best-effort IATA→ICAO mapping for the airports in our dataset.
    OpenSky uses ICAO codes; our coordinate table uses IATA.
    """
    _MAP = {
        "JNB": "FAOR", "CPT": "FACT", "AMS": "EHAM", "GVA": "LSGG",
        "ZRH": "LSZH", "TFN": "GCXO", "TFS": "GCTS", "LHR": "EGLL",
        "CDG": "LFPG", "FRA": "EDDF", "DXB": "OMDB", "SIN": "WSSS",
        "NBO": "HKJK", "JNB": "FAOR", "VCP": "GVSV",
    }
    return _MAP.get(iata.upper(), iata.upper())


def get_departures(airport_iata: str, begin_unix: int, end_unix: int) -> list[FlightRecord]:
    """Return flights departing from ``airport_iata`` between the two timestamps.

    Returns cached data if available; falls back to empty list on error.
    """
    cache_key = (airport_iata.upper(), begin_unix, end_unix)
    entry = _CACHE.get(cache_key)
    if entry is not None and entry.is_fresh():
        return entry.data

    merged: list[FlightRecord] = []
    seen: set[tuple[str, int, str | None]] = set()
    for cb, ce in _utc_day_chunks(begin_unix, end_unix):
        # OpenSky requires end strictly greater than begin.
        api_end = ce if ce > cb else cb + 1
        for rec in _fetch_from_api(airport_iata, cb, api_end):
            key = (rec.callsign, rec.est_departure_time, rec.dest_airport_icao)
            if key in seen:
                continue
            seen.add(key)
            merged.append(rec)

    _CACHE[cache_key] = _CacheEntry(data=merged)
    return merged


def _fetch_from_api(airport_iata: str, begin_unix: int, end_unix: int) -> list[FlightRecord]:
    try:
        from opensky_api import OpenSkyApi  # type: ignore
    except ImportError:
        logger.warning("opensky-api not installed; returning empty departures")
        return []

    try:
        api = OpenSkyApi()
        icao = iata_to_icao(airport_iata)
        flights = api.get_departures_by_airport(icao, begin_unix, end_unix)
        if not flights:
            return []
        records = []
        for f in flights:
            dest_icao = getattr(f, "estArrivalAirport", None)
            records.append(FlightRecord(
                callsign=(getattr(f, "callsign", "") or "").strip(),
                dest_airport_icao=dest_icao,
                dest_airport_iata=_icao_to_iata(dest_icao) if dest_icao else None,
                est_departure_time=getattr(f, "firstSeen", begin_unix),
                est_arrival_time=getattr(f, "lastSeen", None),
            ))
        return records
    except Exception as exc:
        logger.warning("OpenSky API call failed for %s: %s", airport_iata, exc)
        return []


def _icao_to_iata(icao: str) -> str | None:
    """Reverse mapping for the airports relevant to this outbreak."""
    _MAP = {
        "FAOR": "JNB", "FACT": "CPT", "EHAM": "AMS", "LSGG": "GVA",
        "LSZH": "ZRH", "GCXO": "TFN", "GCTS": "TFS", "EGLL": "LHR",
        "LFPG": "CDG", "EDDF": "FRA", "OMDB": "DXB", "WSSS": "SIN",
        "HKJK": "NBO",
    }
    return _MAP.get(icao.upper())
