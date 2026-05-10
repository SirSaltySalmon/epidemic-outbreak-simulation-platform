"""Assembles the geo/outbreak response for the Leaflet world map.

Reads ``spawn_profile.json`` and ``flight_schedules_baseline.json`` for ship /
evacuation map overlays, enriches with bundled airport coordinates, and builds
heatmap layers from the cached ABM ``geo_forecast`` block on the baseline
forecast (ensemble bucket medians).

Case markers come from ingested ``CaseRecord`` rows only. If no baseline
forecast or ``geo_forecast`` is available yet, the primary heat layer is empty
and ``errors`` explains why — there is no live flight-network API on this path.
"""

from __future__ import annotations

from datetime import UTC, datetime

from eosp.core.models import CaseRecord, CaseStatus, ObservationKind
from eosp.services.patch_codes import iata_from_destination
from eosp.services.reference_geo import load_airport_coords

from eosp.services.schedule_baseline import load_baseline_schedule
from eosp.services.world_builder import load_spawn_profile

_ABM_GEO_UNAVAILABLE_SOURCE = "abm_geo_unavailable"


def _case_markers_from_records(cases: list[CaseRecord], airport_coords: dict[str, Any]) -> list[dict[str, Any]]:
    """Aggregate ingested cases by country; place using the first geocoded airport in that country."""

    by_country: dict[str, dict[str, Any]] = {}
    for c in cases:
        cc = c.location_country.upper()
        if cc not in by_country:
            by_country[cc] = {
                "country": cc,
                "confirmed": 0,
                "suspected": 0,
                "deaths": 0,
                "lat": None,
                "lng": None,
                "airport": "",
            }
        b = by_country[cc]
        w = c.cohort_size
        if c.confirmed_or_suspected == CaseStatus.CONFIRMED:
            b["confirmed"] += w
        else:
            b["suspected"] += w
        if c.observation_kind == ObservationKind.COHORT:
            b["deaths"] += c.cohort_deaths
        elif c.death_date is not None:
            b["deaths"] += w
        code = (c.location_airport_code or "").upper()
        if b["lat"] is None and code:
            ac = airport_coords.get(code)
            if ac is not None:
                b["lat"] = float(ac["lat"])
                b["lng"] = float(ac["lng"])
                b["airport"] = code
    out: list[dict[str, Any]] = []
    for b in by_country.values():
        if b["lat"] is None:
            continue
        out.append(
            {
                "country": b["country"],
                "lat": b["lat"],
                "lng": b["lng"],
                "confirmed": b["confirmed"],
                "suspected": b["suspected"],
                "deaths": b["deaths"],
                "airport": b["airport"],
            }
        )
    return out


def _risk_heatmap_rows_from_abm_geo_forecast(
    abm_geo_forecast: dict[str, Any], coords: dict[str, Any]
) -> list[dict[str, Any]]:
    """Build heatmap rows from ABM geo forecast using cumulative infected (final day, median)."""

    by_day = abm_geo_forecast["by_day"]
    last = by_day[-1]
    buckets = last.get("buckets") or {}
    rows: list[dict[str, Any]] = []
    for code, stats in buckets.items():
        if str(code).lower() == "ship":
            continue
        if not isinstance(stats, dict):
            continue
        block = stats.get("cumulative_infected")
        if block is None:
            if "median" in stats and not any(isinstance(v, dict) for v in stats.values()):
                block = stats
        median_v = float(block["median"]) if block and "median" in block else 0.0
        iata = iata_from_destination(code)
        loc = coords.get(iata)
        if loc is None:
            continue
        rows.append({
            "airport_iata": iata,
            "lat": float(loc["lat"]),
            "lng": float(loc["lng"]),
            "city": str(loc.get("city", "")),
            "country": str(loc.get("country", "")),
            "risk_score": median_v,
            "ring": 0,
        })
    return rows


def _abm_layer_features_from_buckets(
    buckets: dict[str, Any],
    metric_key: str,
    coords: dict[str, Any],
) -> list[dict[str, Any]]:
    features: list[dict[str, Any]] = []
    for code, stats in buckets.items():
        if str(code).lower() == "ship":
            continue
        if not isinstance(stats, dict):
            continue
        block = stats.get(metric_key)
        if not isinstance(block, dict) or "median" not in block:
            continue
        iata = iata_from_destination(code)
        loc = coords.get(iata)
        if loc is None:
            continue
        features.append({
            "bucket_code": code,
            "airport_iata": iata,
            "lat": float(loc["lat"]),
            "lng": float(loc["lng"]),
            "city": str(loc.get("city", "")),
            "country": str(loc.get("country", "")),
            "value": float(block["median"]),
            "ci_95_lower": float(block.get("ci_95_lower", 0.0)),
            "ci_95_upper": float(block.get("ci_95_upper", 0.0)),
        })
    return features


def _risk_heatmap_from_layer_features(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "airport_iata": f["airport_iata"],
            "lat": f["lat"],
            "lng": f["lng"],
            "city": f["city"],
            "country": f["country"],
            "risk_score": f["value"],
            "ring": int(f.get("ring", 0)),
        }
        for f in features
    ]


def _legacy_metadata_from_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    """Merge provenance + simulation summary for /geo/outbreak ``metadata`` (no ``layers``)."""

    prov = bundle["provenance"]
    sim = bundle["simulation"]
    meta: dict[str, Any] = {
        **prov,
        "n_simulations": sim.get("n_simulations"),
        "ensemble_spec_hash": sim.get("ensemble_spec_hash"),
        "inference_version": sim.get("inference_version"),
        "scenario": sim.get("scenario"),
        "primary_layer_id": sim.get("primary_layer_id"),
    }
    if "p_transmit_used" in sim:
        meta["p_transmit_used"] = sim["p_transmit_used"]
    for k in (
        "ring1_airports",
        "ring2_airports_found",
        "ring3_airports_found",
        "mobility_bundle_version",
        "mobility_source",
        "mobility_license_note",
        "mobility_horizon_days",
        "risk_metric_id",
    ):
        if k in sim:
            meta[k] = sim[k]
    return meta


def build_geo_bundle(
    *,
    p_transmit: float,
    cases: list[CaseRecord],
    abm_geo_forecast: dict[str, Any] | None = None,
    inference_version: str | None = None,
    scenario: str = "baseline",
    forecast_n_simulations: int | None = None,
    ensemble_spec_hash: str | None = None,
) -> dict[str, Any]:
    """Assemble the unified geo_bundle payload (see docs/spec geo-bundle map API)."""

    spawn = load_spawn_profile()
    baseline = load_baseline_schedule()
    coords = load_airport_coords()
    ui = spawn.get("ui") or {}
    ship_lat = float(ui.get("lat", 20.5))
    ship_lng = float(ui.get("lng", -21.0))
    ship_name = str((spawn.get("ship") or {}).get("name", "MV Hondius"))
    ship_status = str(ui.get("status", "en_route_tenerife"))
    ship = {
        "lat": ship_lat,
        "lng": ship_lng,
        "name": ship_name,
        "status": ship_status,
    }
    confirmed_cases = _case_markers_from_records(cases, coords)
    evacuation_flights: list[dict[str, Any]] = []
    for flight in baseline.get("flights") or []:
        dest_code = str(flight.get("destination_code") or flight.get("destination", ""))
        iata = iata_from_destination(dest_code)
        dest_coords = coords.get(iata)
        if dest_coords is None:
            continue
        evacuation_flights.append({
            "name": str(flight.get("name") or flight.get("key", "")),
            "from_lat": ship_lat,
            "from_lng": ship_lng,
            "to_lat": dest_coords["lat"],
            "to_lng": dest_coords["lng"],
            "to_airport": iata,
            "depart_day": flight.get("depart_day", 0),
            "passengers": flight.get("n_passengers", 0),
        })

    errors: list[dict[str, Any]] = []
    sim_extra: dict[str, Any] = {
        "inference_version": inference_version,
        "n_simulations": forecast_n_simulations,
        "scenario": scenario,
        "ensemble_spec_hash": ensemble_spec_hash,
    }

    by_day = (abm_geo_forecast or {}).get("by_day") or []
    if not by_day:
        primary_layer_id = "abm_geo_pending"
        layers = [
            {
                "id": primary_layer_id,
                "label": "ABM geo forecast unavailable",
                "metric_id": "none",
                "metric_day": "n/a",
                "metric_stat": "n/a",
                "features": [],
            },
        ]
        provenance = {
            "risk_source": _ABM_GEO_UNAVAILABLE_SOURCE,
            "primary_metric_id": primary_layer_id,
            "risk_heatmap_explanation": (
                "No cached baseline forecast with geo buckets yet. Run a baseline simulation from the Console; "
                "the orange heat layer fills once ``geo_forecast`` is stored with the forecast."
            ),
            "distinct_from": (
                "This placeholder has no scores. Populated layers use ABM ensemble medians per destination bucket."
            ),
        }
        errors.append({
            "code": "abm_geo_unavailable",
            "reason": "no_cached_baseline_geo_forecast",
        })
        sim_extra.update(
            {
                "primary_layer_id": primary_layer_id,
                "layers": layers,
                "p_transmit_used": round(p_transmit, 4),
                "ring1_airports": [],
                "ring2_airports_found": 0,
                "ring3_airports_found": 0,
            }
        )
    else:
        last = by_day[-1]
        buckets = last.get("buckets") or {}
        layers = [
            {
                "id": "cumulative_infected_median_final_day",
                "label": "Cumulative infections by destination (final day, median)",
                "metric_id": "cumulative_infected",
                "metric_day": "final",
                "metric_stat": "median",
                "features": _abm_layer_features_from_buckets(
                    buckets, "cumulative_infected", coords
                ),
            },
            {
                "id": "peak_infectious_I_median",
                "label": "Peak concurrent infectious agents by destination (ensemble median)",
                "metric_id": "peak_infectious_I",
                "metric_day": "peak",
                "metric_stat": "median",
                "features": _abm_layer_features_from_buckets(
                    buckets, "peak_infectious_I", coords
                ),
            },
        ]
        primary_layer_id = "cumulative_infected_median_final_day"
        provenance = {
            "risk_source": "abm_geo_forecast",
            "primary_metric_id": primary_layer_id,
            "risk_heatmap_explanation": (
                "ABM ensemble median cumulative infections per destination bucket on the final forecast day. "
                "Model-structured — not confirmed incidence."
            ),
            "distinct_from": (
                "Static itineraries or other overlays may use different movement assumptions than this ABM kernel."
            ),
        }
        sim_extra.update(
            {
                "primary_layer_id": primary_layer_id,
                "layers": layers,
                "p_transmit_used": round(p_transmit, 4),
                "ring1_airports": [],
                "ring2_airports_found": 0,
                "ring3_airports_found": 0,
                "risk_metric_id": primary_layer_id,
            }
        )

    simulation = {
        "ensemble_spec_hash": sim_extra.get("ensemble_spec_hash"),
        "inference_version": sim_extra.get("inference_version"),
        "n_simulations": sim_extra.get("n_simulations"),
        "scenario": sim_extra.get("scenario"),
        "primary_layer_id": sim_extra["primary_layer_id"],
        "layers": sim_extra["layers"],
        "p_transmit_used": sim_extra.get("p_transmit_used"),
        "ring1_airports": sim_extra.get("ring1_airports", []),
        "ring2_airports_found": sim_extra.get("ring2_airports_found", 0),
        "ring3_airports_found": sim_extra.get("ring3_airports_found", 0),
    }
    for k in (
        "mobility_bundle_version",
        "mobility_source",
        "mobility_license_note",
        "mobility_horizon_days",
        "risk_metric_id",
    ):
        if k in sim_extra:
            simulation[k] = sim_extra[k]

    return {
        "schema_version": "1",
        "as_of_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "observed": {"case_markers": confirmed_cases, "ingest_cursor": None},
        "simulation": simulation,
        "provenance": provenance,
        "evacuation_flights": evacuation_flights,
        "ship": ship,
        "errors": errors,
    }


def outbreak_dict_from_geo_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    """Legacy /geo/outbreak shape from a geo_bundle."""

    primary_layer = next(
        layer
        for layer in bundle["simulation"]["layers"]
        if layer["id"] == bundle["simulation"]["primary_layer_id"]
    )
    risk_heatmap = _risk_heatmap_from_layer_features(primary_layer["features"])
    meta = _legacy_metadata_from_bundle(bundle)
    return {
        "ship": bundle["ship"],
        "confirmed_cases": bundle["observed"]["case_markers"],
        "evacuation_flights": bundle["evacuation_flights"],
        "risk_heatmap": risk_heatmap,
        "metadata": meta,
    }


def build_outbreak_geo(
    *,
    p_transmit: float,
    cases: list[CaseRecord],
    abm_geo_forecast: dict[str, Any] | None = None,
    inference_version: str | None = None,
    forecast_n_simulations: int | None = None,
    ensemble_spec_hash: str | None = None,
    scenario: str = "baseline",
) -> dict[str, Any]:
    """Build the complete geo/outbreak payload.

    ``p_transmit`` should come from the latest inference when available; callers
    pass a neutral default (e.g. prior mean) when there is no posterior yet.
    Map markers reflect ``cases`` only.
    """

    bundle = build_geo_bundle(
        p_transmit=p_transmit,
        cases=cases,
        abm_geo_forecast=abm_geo_forecast,
        inference_version=inference_version,
        scenario=scenario,
        forecast_n_simulations=forecast_n_simulations,
        ensemble_spec_hash=ensemble_spec_hash,
    )
    return outbreak_dict_from_geo_bundle(bundle)
