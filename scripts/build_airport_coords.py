"""Download OurAirports CSV and write app/eosp/data/airport_coords.json.

Run from repo root: python scripts/build_airport_coords.py

Source: https://ourairports.com/data/ (same dataset as davidmegginson/ourairports-data).
"""

from __future__ import annotations

import csv
import json
import urllib.request
from pathlib import Path

OURAIRPORTS_URL = (
    "https://raw.githubusercontent.com/davidmegginson/ourairports-data/main/airports.csv"
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "app" / "eosp" / "data"
CSV_PATH = DATA / "_airports.csv"
OUT_PATH = DATA / "airport_coords.json"

_TYPE_PRIORITY = {"large_airport": 3, "medium_airport": 2, "small_airport": 1, "seaplane_base": 1, "heliport": 0, "closed": -1}


def fetch_csv() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(OURAIRPORTS_URL, CSV_PATH)


def build_json() -> int:
    airports: dict[str, dict] = {}
    with CSV_PATH.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            iata = (row.get("iata_code") or "").strip().upper()
            if len(iata) != 3 or not iata.isalpha():
                continue
            cc = (row.get("iso_country") or "").strip().upper()
            if len(cc) != 2:
                continue
            try:
                lat_f = float(row["latitude_deg"])
                lon_f = float(row["longitude_deg"])
            except (KeyError, TypeError, ValueError):
                continue
            typ = row.get("type") or ""
            pri = _TYPE_PRIORITY.get(typ, 0)
            city = (row.get("municipality") or "").strip()
            if not city:
                city = (row.get("name") or "").strip()
            entry = {
                "lat": round(lat_f, 6),
                "lng": round(lon_f, 6),
                "city": city[:160],
                "country": cc,
                "_pri": pri,
            }
            prev = airports.get(iata)
            if prev is not None and prev["_pri"] >= pri:
                continue
            airports[iata] = entry

    for v in airports.values():
        del v["_pri"]

    ordered = dict(sorted(airports.items()))
    OUT_PATH.write_text(json.dumps(ordered, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(ordered)


def main() -> None:
    print(f"Fetching {OURAIRPORTS_URL} …")
    fetch_csv()
    n = build_json()
    print(f"Wrote {OUT_PATH} ({n} IATA airports)")


if __name__ == "__main__":
    main()
