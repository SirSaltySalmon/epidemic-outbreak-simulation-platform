import numpy as np

from eosp.services.network import apply_modifications, build_default_network

_MINI_LEGACY_SPEC: dict = {
    "rng_seed": 1,
    "gateway_pseudocount": 0.5,
    "ship": {
        "n_passengers": 5,
        "n_crew": 2,
        "active_window": {"start_day": 0, "end_day": 10},
        "cabins": {"n_cabins": 3, "occupancy": 2, "household_weight": 0.8},
        "crew_workgroups": {"n_groups": 1, "members_per_group": 2, "workplace_weight": 0.85},
        "social": {
            "social_weight": 0.4,
            "mean_social_contacts": 2,
            "transient_weight": 0.05,
            "mean_transient_contacts": 2,
        },
    },
    "edge_weights": {
        "household": 0.8,
        "workplace": 0.85,
        "social": 0.4,
        "transient": 0.05,
        "hospital": 0.5,
        "family": 0.5,
    },
    "flights": [
        {
            "name": "f1",
            "destination": "ZA_JNB",
            "depart_day": 1,
            "n_passengers": 5,
            "edge_weight": 0.5,
            "neighbour_radius": 3,
        }
    ],
    "destinations": [
        {
            "name": "jnb",
            "destination": "ZA_JNB",
            "n_healthcare_workers": 2,
            "n_family_contacts": 1,
        }
    ],
}


def test_default_network_is_hub_itinerary_cohort():
    network = build_default_network()
    assert network.n_agents == 147
    assert network.ship_node_indices() == []
    assert network.layers == []
    assert network.itinerary_contact_patch is not None
    assert network.itinerary_bucket_labels is not None


def test_adjacency_for_day_yields_symmetric_sparse_matrix():
    network = build_default_network()
    adjacency = network.adjacency_for_day(0)
    assert adjacency.shape == (network.n_agents, network.n_agents)
    dense = adjacency.toarray()
    assert np.allclose(dense, dense.T)
    if not network.layers:
        assert adjacency.nnz == 0
    else:
        assert adjacency.nnz > 0


def test_apply_modifications_drops_edge_types_on_legacy_spec():
    network = build_default_network(spec=_MINI_LEGACY_SPEC, n_days=14)
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
