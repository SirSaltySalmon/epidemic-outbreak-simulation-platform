"""Unit tests for OpenFlights route parsing and domestic-biased sampling."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from eosp.services.openflights_routes import (
    load_iata_to_country,
    outbound_weights_from_counts,
    parse_route_edge_counts,
    sample_next_airport,
)

FIXT = Path(__file__).resolve().parent / "fixtures" / "openflights_mini"


def test_parse_route_edge_counts_builds_adjacency():
    c = parse_route_edge_counts(FIXT / "routes.dat")
    assert c[("JNB", "CPT")] >= 1
    assert c[("JNB", "DOH")] >= 1


def test_domestic_bias_prefers_same_country():
    ec = parse_route_edge_counts(FIXT / "routes.dat")
    outbound = outbound_weights_from_counts(ec)
    ap_map = load_iata_to_country(FIXT / "airports.dat")
    rng = np.random.default_rng(42)
    for _ in range(30):
        nxt = sample_next_airport(
            rng,
            outbound,
            "JNB",
            iata_to_country=ap_map,
            domestic_bias=1.0,
        )
        assert nxt == "CPT"


def test_full_outbound_can_reach_international():
    ec = parse_route_edge_counts(FIXT / "routes.dat")
    outbound = outbound_weights_from_counts(ec)
    ap_map = load_iata_to_country(FIXT / "airports.dat")
    rng = np.random.default_rng(0)
    hits = {sample_next_airport(rng, outbound, "JNB", iata_to_country=ap_map, domestic_bias=0.0) for _ in range(40)}
    assert "DOH" in hits
