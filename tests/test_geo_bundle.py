"""Tests for unified geo_bundle API (see docs/superpowers/specs/2026-05-09-geo-bundle-map-api-design.md)."""

from eosp.core.seed_data import CASES
from eosp.services.geo import build_geo_bundle, outbreak_dict_from_geo_bundle


def _minimal_abm_forecast():
    return {
        "by_day": [
            {
                "day": 14,
                "buckets": {
                    "ZA_JNB": {
                        "cumulative_infected": {
                            "median": 12.4,
                            "ci_95_lower": 5.0,
                            "ci_95_upper": 28.3,
                        },
                        "infectious_I": {"median": 0.0, "ci_95_lower": 0.0, "ci_95_upper": 1.0},
                        "peak_infectious_I": {"median": 7.0, "ci_95_lower": 2.0, "ci_95_upper": 15.0},
                    },
                },
            },
        ],
    }


def test_geo_bundle_schema_version():
    b = build_geo_bundle(
        p_transmit=0.1,
        cases=list(CASES),
        abm_geo_forecast=_minimal_abm_forecast(),
        inference_version="v-test",
        forecast_n_simulations=100,
        ensemble_spec_hash="abc123",
    )
    assert b["schema_version"] == "1"


def test_geo_bundle_primary_layer_references_valid_id():
    b = build_geo_bundle(
        p_transmit=0.1,
        cases=list(CASES),
        abm_geo_forecast=_minimal_abm_forecast(),
    )
    pid = b["simulation"]["primary_layer_id"]
    ids = {layer["id"] for layer in b["simulation"]["layers"]}
    assert pid in ids


def test_geo_bundle_cumulative_metric_is_never_blank():
    b = build_geo_bundle(
        p_transmit=0.1,
        cases=list(CASES),
        abm_geo_forecast=_minimal_abm_forecast(),
    )
    primary = next(
        layer
        for layer in b["simulation"]["layers"]
        if layer["id"] == b["simulation"]["primary_layer_id"]
    )
    assert any(f.get("value", 0) > 0 for f in primary["features"])


def test_geo_bundle_peak_infectious_I_present():
    b = build_geo_bundle(
        p_transmit=0.1,
        cases=list(CASES),
        abm_geo_forecast=_minimal_abm_forecast(),
    )
    peak_layer = next(layer for layer in b["simulation"]["layers"] if layer["id"] == "peak_infectious_I_median")
    assert peak_layer["features"]
    f0 = peak_layer["features"][0]
    assert "ci_95_lower" in f0 and "ci_95_upper" in f0


def test_legacy_endpoint_compat():
    b = build_geo_bundle(
        p_transmit=0.1,
        cases=list(CASES),
        abm_geo_forecast=_minimal_abm_forecast(),
    )
    legacy = outbreak_dict_from_geo_bundle(b)
    row = legacy["risk_heatmap"][0]
    for key in ("airport_iata", "lat", "lng", "city", "country", "risk_score", "ring"):
        assert key in row


def test_geo_bundle_missing_forecast_produces_errors_array():
    b = build_geo_bundle(
        p_transmit=0.1,
        cases=list(CASES),
        abm_geo_forecast=None,
    )
    assert b["errors"]
    assert b["errors"][0].get("code") == "abm_geo_unavailable"
