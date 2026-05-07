import numpy as np

from eosp.services.network import apply_modifications, build_default_network


def test_default_network_has_ship_population():
    network = build_default_network()
    assert network.n_agents > 147  # ship + destination clusters
    ship_indices = network.ship_node_indices()
    assert len(ship_indices) == 147

    layer_types = {layer.type for layer in network.layers}
    assert {"household", "workplace", "social", "transient", "flight", "hospital", "family"}.issubset(layer_types)


def test_adjacency_for_day_yields_symmetric_sparse_matrix():
    network = build_default_network()
    adjacency = network.adjacency_for_day(0)
    assert adjacency.shape == (network.n_agents, network.n_agents)
    assert adjacency.nnz > 0
    dense = adjacency.toarray()
    assert np.allclose(dense, dense.T)


def test_apply_modifications_drops_edge_types():
    network = build_default_network()
    modified = apply_modifications(
        network,
        {"remove_edges": ["social", "transient"], "modify_weights": {"household": 0.5}},
    )
    types_remaining = {layer.type for layer in modified.layers}
    assert "social" not in types_remaining
    assert "transient" not in types_remaining

    household_layers = [layer for layer in modified.layers if layer.type == "household"]
    original_households = [layer for layer in network.layers if layer.type == "household"]
    assert household_layers and original_households
    original_weight = original_households[0].edges[0][2]
    modified_weight = household_layers[0].edges[0][2]
    assert abs(modified_weight - original_weight * 0.5) < 1e-9


def test_degree_summary_returns_positive_means():
    network = build_default_network()
    summary = network.degree_summary()
    assert summary["mean_weighted_degree"] > 0
    assert summary["max_weighted_degree"] >= summary["mean_weighted_degree"]
