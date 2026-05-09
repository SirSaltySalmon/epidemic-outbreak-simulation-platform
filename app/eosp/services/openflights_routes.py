"""Parse OpenFlights ``routes.dat`` / ``airports.dat`` and sample onward movement."""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

import numpy as np


def parse_route_edge_counts(routes_path: Path) -> Counter[tuple[str, str]]:
    """Count weekly route rows as integer weights on directed (src_iata, dst_iata) edges."""

    out: Counter[tuple[str, str]] = Counter()
    path = Path(routes_path)
    with path.open(encoding="utf-8", errors="replace", newline="") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            row = next(csv.reader([line]))
            if len(row) < 5:
                continue
            src = row[2].strip().strip('"').upper()
            dst = row[4].strip().strip('"').upper()
            if not src or not dst or src in {"\\N", "N"} or dst in {"\\N", "N"}:
                continue
            if len(src) != 3 or not src.isalpha() or len(dst) != 3 or not dst.isalpha():
                continue
            if src == dst:
                continue
            out[(src, dst)] += 1
    return out


def outbound_weights_from_counts(
    edge_counts: Counter[tuple[str, str]],
) -> dict[str, dict[str, int]]:
    """Adjacency list with integer weights (duplicate route rows summed)."""

    adj: dict[str, dict[str, int]] = {}
    for (a, b), w in edge_counts.items():
        inner = adj.setdefault(a, {})
        inner[b] = inner.get(b, 0) + int(w)
    return adj


def load_iata_to_country(airports_path: Path) -> dict[str, str]:
    """Map IATA → country label from OpenFlights ``airports.dat`` (column 3 = country)."""

    out: dict[str, str] = {}
    path = Path(airports_path)
    with path.open(encoding="utf-8", errors="replace", newline="") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            row = next(csv.reader([line]))
            if len(row) < 5:
                continue
            country = row[3].strip().strip('"')
            iata = row[4].strip().strip('"').upper()
            if len(iata) != 3 or not iata.isalpha() or iata in {"\\N", "N"}:
                continue
            out[iata] = country
    return out


def all_iatas_from_counts(edge_counts: Counter[tuple[str, str]]) -> set[str]:
    codes: set[str] = set()
    for (a, b) in edge_counts.keys():
        codes.add(a)
        codes.add(b)
    return codes


def sample_next_airport(
    rng: np.random.Generator,
    outbound: dict[str, dict[str, int]],
    current_iata: str,
    *,
    iata_to_country: dict[str, str],
    domestic_bias: float,
) -> str:
    """Sample a destination weighted by route row counts.

    With probability ``domestic_bias``, restrict to same-country destinations; if none
    exist, **stay** at ``current_iata`` (no silent international fallback). Empty
    outbound also **stay**s.
    """

    cur_u = str(current_iata).upper()
    outs = outbound.get(cur_u)
    if not outs:
        return cur_u
    use: dict[str, int] = dict(outs)
    bias = float(np.clip(domestic_bias, 0.0, 1.0))
    if bias > 0.0 and rng.random() < bias:
        cc = iata_to_country.get(cur_u, "")
        domestic = {d: w for d, w in use.items() if cc and iata_to_country.get(d) == cc}
        if domestic:
            use = domestic
        else:
            return cur_u
    pairs = sorted(use.items(), key=lambda t: t[0])
    weights = np.array([float(w) for _, w in pairs], dtype=float)
    total = float(weights.sum())
    if total <= 0.0:
        return cur_u
    pick = int(rng.choice(len(weights), p=weights / total))
    return str(pairs[pick][0])
