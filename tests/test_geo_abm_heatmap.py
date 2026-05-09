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


def test_build_outbreak_geo_without_forecast_has_empty_heatmap():
    from eosp.core.seed_data import CASES
    from eosp.services.geo import build_outbreak_geo

    out = build_outbreak_geo(
        p_transmit=0.1,
        cases=list(CASES),
        abm_geo_forecast=None,
    )
    assert out["metadata"].get("risk_source") == "abm_geo_unavailable"
    assert out["risk_heatmap"] == []
