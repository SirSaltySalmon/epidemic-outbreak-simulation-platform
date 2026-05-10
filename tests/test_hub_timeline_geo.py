from datetime import date

import numpy as np

from eosp.services.geo import _risk_heatmap_rows_from_abm_geo_forecast
from eosp.services.hub_timeline.geo_output import build_geo_forecast_dict
from eosp.services.hub_timeline.kernel import HubTrajectorySnapshot


def test_geo_forecast_matches_heatmap_extractor():
    dates = [date(2026, 3, 1), date(2026, 3, 2)]
    hub = "JNB"
    cum1 = np.array([[1.0], [3.0]])
    cum2 = np.array([[2.0], [4.0]])
    a = np.zeros_like(cum1)
    trajs = [
        HubTrajectorySnapshot(
            dates=dates,
            hubs=[hub],
            hub_index={hub: 0},
            cumulative_infected=cum1,
            infectious_A=a,
            peak_unweighted_PI=np.array([2.0]),
            global_cases=np.array([1.0, 2.0]),
            global_deaths=np.array([0.0, 0.0]),
        ),
        HubTrajectorySnapshot(
            dates=dates,
            hubs=[hub],
            hub_index={hub: 0},
            cumulative_infected=cum2,
            infectious_A=a,
            peak_unweighted_PI=np.array([3.0]),
            global_cases=np.array([2.0, 3.0]),
            global_deaths=np.array([0.0, 0.0]),
        ),
    ]
    i2c = {hub: "ZA"}
    geo = build_geo_forecast_dict(trajs, iata_to_country=i2c)
    coords = {"JNB": {"lat": -26.1, "lng": 28.2, "city": "Johannesburg", "country": "ZA"}}
    rows = _risk_heatmap_rows_from_abm_geo_forecast(geo, coords)
    assert len(rows) == 1
    assert rows[0]["risk_score"] == 3.5
