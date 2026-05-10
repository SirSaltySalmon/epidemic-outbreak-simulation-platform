from eosp.services.network import build_default_network
from eosp.services.place_graph import build_place_graph_from_baseline
from eosp.services.schedule_baseline import load_baseline_schedule


def test_default_world_has_eighty_hub_agents_empty_layers():
    net = build_default_network()
    assert net.n_agents == 147
    assert net.layers == []


def test_place_graph_lists_four_hubs_without_ship():
    baseline = load_baseline_schedule()
    g = build_place_graph_from_baseline(baseline)
    assert "SHIP" not in g.node_ids
    for code in ("JNB", "DOH", "ZRH", "AMS"):
        assert code in g.node_ids
