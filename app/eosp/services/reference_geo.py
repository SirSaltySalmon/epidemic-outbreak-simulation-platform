"""Country/airport reference lists from bundled JSON (dashboard dropdowns + validation)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parents[1] / "data"


@lru_cache
def _airport_coords() -> dict:
    with open(_DATA_DIR / "airport_coords.json", encoding="utf-8") as f:
        return json.load(f)


@lru_cache
def _iso_labels() -> dict[str, str]:
    p = _DATA_DIR / "iso_3166_alpha2_labels.json"
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def all_valid_country_codes() -> set[str]:
    """ISO-3166 alpha-2 codes accepted for geographic validation (airports bundle ∪ label file)."""

    airports = _airport_coords()
    codes = {meta["country"] for meta in airports.values() if "country" in meta}
    codes |= set(_iso_labels().keys())
    return codes


def countries_for_api() -> list[dict[str, str]]:
    labels = _iso_labels()
    codes = all_valid_country_codes()
    return [{"code": c, "name": labels.get(c, c)} for c in sorted(codes)]


def airports_for_api(country: str | None) -> list[dict[str, str]]:
    cc = (country or "").strip().upper()
    airports = _airport_coords()
    rows = []
    for iata, meta in airports.items():
        if cc and meta.get("country", "").upper() != cc:
            continue
        city = meta.get("city", "")
        country_code = meta.get("country", "")
        rows.append({
            "iata": iata,
            "city": city,
            "country": country_code,
            "label": f"{iata} · {city}" + (f" ({country_code})" if country_code else ""),
        })
    rows.sort(key=lambda x: x["iata"])
    return rows
