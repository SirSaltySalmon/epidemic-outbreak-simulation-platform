"""Build EOSP mobility JSON (+ meta, optional Parquet) from OpenFlights-style routes.csv."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MOBILITY_VERSION = 1
SEED_PATCH = "NSEED"


def _valid_iata(code: str) -> bool:
    c = code.strip().upper()
    return len(c) == 3 and c.isalpha()


def _src_dest_from_row(row: list[str]) -> tuple[str, str] | None:
    """Resolve source/dest IATA columns.

    Standard OpenFlights ``routes.dat`` (9+ columns) uses indices 2 and 4.
    A stripped 8-column layout (no airline id) uses indices 1 and 3.
    """
    if len(row) >= 9:
        return row[2], row[4]
    if len(row) >= 8:
        return row[1], row[3]
    return None


def _parse_routes(routes_path: Path) -> Counter[tuple[str, str]]:
    """Return weekly frequency counts per directed (src, dest) IATA pair."""
    counts: Counter[tuple[str, str]] = Counter()
    with routes_path.open(encoding="utf-8", newline="") as f:
        for row in csv.reader(f):
            if not row:
                continue
            pair = _src_dest_from_row(row)
            if pair is None:
                continue
            raw_src, raw_dest = pair
            src = raw_src.strip()
            dest = raw_dest.strip()
            if not src or not dest or src == r"\N" or dest == r"\N":
                continue
            if not _valid_iata(src) or not _valid_iata(dest):
                continue
            a, b = src.upper(), dest.upper()
            counts[(a, b)] += 1
    return counts


def _daily_n(weekly_count: int, default_seat_proxy: float) -> float:
    return float(max(1, round((weekly_count / 7.0) * default_seat_proxy)))


def build_bundle(
    routes_path: Path,
    *,
    n_days: int,
    out_json: Path,
    out_parquet: Path | None,
    default_seat_proxy: float,
) -> dict[str, Any]:
    """Aggregate routes into a mobility bundle JSON dict and write artifacts."""
    weekly = _parse_routes(routes_path)
    iatas: set[str] = set()
    for (a, b), _ in weekly.items():
        iatas.add(a)
        iatas.add(b)

    patch_ids: tuple[str, ...] = (SEED_PATCH,) + tuple(sorted(iatas))

    flow_edges: list[dict[str, Any]] = []
    for (src, dest) in sorted(weekly.keys()):
        w = weekly[(src, dest)]
        n = _daily_n(w, default_seat_proxy)
        flow_edges.append({"from": src, "to": dest, "n": n})

    day_obj = {"flows": flow_edges}
    bundle: dict[str, Any] = {
        "version": MOBILITY_VERSION,
        "patch_ids": list(patch_ids),
        "days": [day_obj for _ in range(n_days)],
    }

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")

    meta_path = out_json.with_suffix(".meta.json")
    meta = {
        "schema_version": SCHEMA_VERSION,
        "mobility_source": "openflights_routes_derived",
        "source_licenses": ["OpenFlights ODbL (user-supplied raw)"],
        "generated_at": datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "horizon_days": n_days,
        "assumptions": [
            "Duplicate route rows counted as weekly frequency; daily n uses weekly/7 times seat proxy.",
            "Seat proxy is a crude capacity stand-in, not observed seats.",
            "Flows repeat identically across each simulated day (v1).",
            "NSEED appears in patch_ids only; no synthetic NSEED OD edges in v1.",
        ],
        "patch_id_order": list(patch_ids),
    }
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    if out_parquet is not None:
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError:
            print(
                "Writing Parquet requires pyarrow; install with: pip install 'eosp[mobility]'",
                file=sys.stderr,
            )
            raise SystemExit(1) from None

        pid_meta = ",".join(patch_ids).encode("utf-8")
        schema = pa.schema(
            [
                ("day", pa.int32()),
                ("origin_iata", pa.string()),
                ("dest_iata", pa.string()),
                ("n", pa.float64()),
            ],
            metadata={b"eosp.patch_ids": pid_meta},
        )
        days: list[int] = []
        origins: list[str] = []
        dests: list[str] = []
        ns: list[float] = []
        for d in range(n_days):
            for fe in flow_edges:
                days.append(d)
                origins.append(fe["from"])
                dests.append(fe["to"])
                ns.append(float(fe["n"]))
        table = pa.table(
            {"day": days, "origin_iata": origins, "dest_iata": dests, "n": ns},
            schema=schema,
        )
        out_parquet.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, out_parquet)

    return bundle


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description=(
            "Build mobility_bundle JSON (+ .meta.json) from an OpenFlights-style routes file. "
            "Place raw OpenFlights routes.dat under data/raw/openflights/ (gitignored)."
        )
    )
    p.add_argument("--routes", type=Path, required=True, help="Path to routes.dat / routes CSV")
    p.add_argument("--n-days", type=int, default=14, dest="n_days", help="Simulated horizon days")
    p.add_argument("--out-json", type=Path, required=True, help="Output mobility JSON path")
    p.add_argument("--out-parquet", type=Path, default=None, help="Optional Parquet long table output")
    p.add_argument(
        "--default-seat-proxy",
        type=float,
        default=150.0,
        dest="default_seat_proxy",
        help="Crude seats-per-week scale for capacity proxy",
    )
    args = p.parse_args(argv)

    build_bundle(
        args.routes,
        n_days=args.n_days,
        out_json=args.out_json,
        out_parquet=args.out_parquet,
        default_seat_proxy=args.default_seat_proxy,
    )


if __name__ == "__main__":
    main()
