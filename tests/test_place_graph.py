from pathlib import Path

from eosp.services.network import load_spec
from eosp.services.place_graph import build_place_graph_from_network_spec


def test_place_graph_includes_ship_and_destination_iatas():
    spec_path = Path(__file__).resolve().parents[1] / "app" / "eosp" / "data" / "network_spec.json"
    spec = load_spec(spec_path)
    g = build_place_graph_from_network_spec(spec)
    assert "SHIP" in g.node_ids
    assert "JNB" in g.node_ids  # from ZA_JNB
    assert "AMS" in g.node_ids
