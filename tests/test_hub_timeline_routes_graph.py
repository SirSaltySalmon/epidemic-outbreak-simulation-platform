from pathlib import Path

import numpy as np

from eosp.services.hub_timeline.routes_graph import build_world_graph, sample_home_airport

FIXT = Path(__file__).resolve().parent / "fixtures" / "openflights_mini"


def test_build_world_graph_smoke():
    allowed, outbound, i2c, dest_w = build_world_graph(FIXT / "routes.dat", FIXT / "airports.dat")
    assert "JNB" in allowed
    assert "JNB" in outbound
    assert dest_w["CPT"] >= 1


def test_duplicate_route_rows_boost_destination_mass():
    # fixture uses multiplicities; global dest pool should reflect CPT mass from JNB edges
    _, _, _, dest_w = build_world_graph(FIXT / "routes.dat", FIXT / "airports.dat")
    rng = np.random.default_rng(0)
    hits = sum(1 for _ in range(8000) if sample_home_airport(rng, "JNB", dest_weights=dest_w, allowed=frozenset(dest_w.keys())) == "CPT")
    assert hits > 100
