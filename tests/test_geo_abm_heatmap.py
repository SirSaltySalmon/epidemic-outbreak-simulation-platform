from eosp.services.geo import _risk_heatmap_rows_from_abm_geo_forecast


def test_heatmap_rows_from_abm_geo_uses_cumulative_median():
    coords = {
        "JNB": {"lat": -26.1, "lng": 28.2, "city": "Johannesburg", "country": "ZA"},
    }
    gf = {
        "by_day": [
            {
                "day": 14,
                "buckets": {
                    "ZA_JNB": {
                        "cumulative_infected": {"median": 10.0},
                        "infectious_I": {"median": 5.0},
                    },
                },
            },
        ],
    }
    rows = _risk_heatmap_rows_from_abm_geo_forecast(gf, coords)
    assert len(rows) == 1
    assert rows[0]["airport_iata"] == "JNB"
    assert rows[0]["risk_score"] == 10.0
    assert rows[0]["lat"] == -26.1


def test_build_outbreak_geo_abm_geo_fallback_without_forecast():
    from eosp.core.seed_data import CASES
    from eosp.services.geo import build_outbreak_geo

    out = build_outbreak_geo(
        p_transmit=0.1,
        cases=list(CASES),
        risk_model="abm_geo",
        abm_geo_forecast=None,
    )
    assert out["metadata"].get("abm_geo_fallback_reason")
    assert "risk_heatmap" in out and len(out["risk_heatmap"]) > 0
