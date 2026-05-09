"""Assembles the geo/outbreak response for the Leaflet world map.

Reads static network_spec.json for ship/flight topology,
enriches with airport coordinates, and calls risk_propagation to
build the global heatmap. Case markers are derived from ingested
``CaseRecord`` rows only (no hardcoded outbreak counts).

``risk_model`` selects the heatmap kernel: ``"legacy"`` prefers a **cached** OpenSky flight-ring
snapshot from the last baseline simulation (Ring‑1‑only hubs if none); ``"metapop"`` uses the stochastic
metapop ensemble; ``"abm_geo"`` uses cached ABM ``geo_forecast`` bucket medians when available,
otherwise the same legacy snapshot / hub fallback as above. Any other value is treated as
``"legacy"``.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from eosp.core.models import CaseRecord, CaseStatus, ObservationKind
from eosp.services.metapop import MetapopParams, run_ensemble_metapop
from eosp.services.patch_codes import iata_from_destination
from eosp.services.metapop_seed import build_initial_metapop_state
from eosp.services.mobility import load_mobility_schedule, load_mobility_sidecar_meta
from eosp.services.opensky import load_airport_coords
from eosp.services.risk_propagation import compute_ring1_zones_only, compute_risk_zones

_SPEC_PATH = Path(__file__).resolve().parent.parent / "data" / "network_spec.json"
_DEFAULT_METAPOP_MOBILITY = Path(__file__).resolve().parent.parent / "data" / "mobility_weekly_skeleton.json"

# MV Hondius approximate position — en route Cape Verde → Tenerife
_SHIP_LAT = 20.5
_SHIP_LNG = -21.0


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
        if code == "ship":
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
        if code == "ship":
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


def _heatmap_layer_from_rows(
    *,
    layer_id: str,
    label: str,
    metric_id: str,
    metric_day: str,
    metric_stat: str,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    features: list[dict[str, Any]] = []
    for z in rows:
        rs = float(z.get("risk_score") or 0.0)
        features.append({
            "bucket_code": str(z.get("bucket_code") or z["airport_iata"]),
            "airport_iata": z["airport_iata"],
            "lat": float(z["lat"]),
            "lng": float(z["lng"]),
            "city": str(z.get("city", "")),
            "country": str(z.get("country", "")),
            "value": rs,
            "ci_95_lower": rs,
            "ci_95_upper": rs,
            "ring": int(z.get("ring", 0)),
        })
    return {
        "id": layer_id,
        "label": label,
        "metric_id": metric_id,
        "metric_day": metric_day,
        "metric_stat": metric_stat,
        "features": features,
    }


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
    # Ring/kernel fields when present on primary features path
    if "p_transmit_used" in sim:
        meta["p_transmit_used"] = sim["p_transmit_used"]
    for k in (
        "ring1_airports",
        "ring2_airports_found",
        "ring3_airports_found",
        "abm_geo_fallback_reason",
        "metapop_n_runs",
        "metapop_mobility_path",
        "mobility_bundle_version",
        "mobility_source",
        "mobility_license_note",
        "mobility_horizon_days",
    ):
        if k in sim:
            meta[k] = sim[k]
    return meta


def build_geo_bundle(
    *,
    p_transmit: float,
    cases: list[CaseRecord],
    risk_model: str = "legacy",
    metapop_n_runs: int | None = None,
    metapop_mobility_path: Path | None = None,
    ship_outbreak_mass: float | None = None,
    metapop_progress_callback: Callable[[dict[str, Any]], None] | None = None,
    abm_geo_forecast: dict[str, Any] | None = None,
    legacy_opensky_geo: dict[str, Any] | None = None,
    inference_version: str | None = None,
    scenario: str = "baseline",
    forecast_n_simulations: int | None = None,
    ensemble_spec_hash: str | None = None,
) -> dict[str, Any]:
    """Assemble the unified geo_bundle payload (see docs/spec geo-bundle map API)."""

    spec = _load_spec()
    coords = load_airport_coords()
    ship = {
        "lat": _SHIP_LAT,
        "lng": _SHIP_LNG,
        "name": spec.get("ship", {}).get("name", "MV Hondius"),
        "status": "en_route_tenerife",
    }
    confirmed_cases = _case_markers_from_records(cases, coords)
    evacuation_flights: list[dict[str, Any]] = []
    for flight in spec.get("flights", []):
        dest_code = flight.get("destination", "")
        iata = dest_code.split("_")[-1] if "_" in dest_code else dest_code
        dest_coords = coords.get(iata)
        if dest_coords is None:
            continue
        evacuation_flights.append({
            "name": flight.get("name", ""),
            "from_lat": _SHIP_LAT,
            "from_lng": _SHIP_LNG,
            "to_lat": dest_coords["lat"],
            "to_lng": dest_coords["lng"],
            "to_airport": iata,
            "depart_day": flight.get("depart_day", 0),
            "passengers": flight.get("n_passengers", 0),
        })

    rm = (risk_model or "legacy").strip().lower()
    errors: list[dict[str, Any]] = []
    layers: list[dict[str, Any]]
    primary_layer_id: str
    provenance: dict[str, Any]
    sim_extra: dict[str, Any] = {
        "inference_version": inference_version,
        "n_simulations": forecast_n_simulations,
        "scenario": scenario,
        "ensemble_spec_hash": ensemble_spec_hash,
    }

    if rm == "abm_geo":
        by_day = (abm_geo_forecast or {}).get("by_day") or []
        if not by_day:
            heatmap, meta = _resolve_legacy_opensky_heatmap(p_transmit, legacy_opensky_geo)
            fb = str(meta.get("risk_source", "legacy_opensky_fallback"))
            errors.append({
                "code": "abm_geo_unavailable",
                "reason": "no_cached_baseline_geo_forecast",
                "fallback": fb,
            })
            primary_layer_id = "legacy_opensky_primary"
            layers = [
                _heatmap_layer_from_rows(
                    layer_id=primary_layer_id,
                    label="OpenSky flight-ring heuristic (fallback)",
                    metric_id="legacy_opensky",
                    metric_day="n/a",
                    metric_stat="n/a",
                    rows=heatmap,
                ),
            ]
            base_expl = str(meta["risk_heatmap_explanation"])
            meta = dict(meta)
            meta["abm_geo_fallback_reason"] = "no_cached_baseline_geo_forecast"
            meta["risk_heatmap_explanation"] = (
                base_expl
                + " Fallback: ABM geo forecast was unavailable; showing cached flight rings or hub-only heatmap."
            )
            provenance = {
                "risk_source": meta["risk_source"],
                "primary_metric_id": primary_layer_id,
                "risk_heatmap_explanation": meta["risk_heatmap_explanation"],
                "distinct_from": (
                    "ABM bucket ensemble uses agent counts per destination; this layer is the OpenSky ring heuristic."
                ),
            }
            sim_extra.update(
                {
                    "primary_layer_id": primary_layer_id,
                    "layers": layers,
                    "p_transmit_used": meta.get("p_transmit_used", round(p_transmit, 4)),
                    "ring1_airports": meta.get("ring1_airports", []),
                    "ring2_airports_found": meta.get("ring2_airports_found", 0),
                    "ring3_airports_found": meta.get("ring3_airports_found", 0),
                    "abm_geo_fallback_reason": meta.get("abm_geo_fallback_reason"),
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
                    "Legacy OpenSky ring heat uses flight connectivity heuristics, not agent counts."
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
    elif rm == "metapop":
        if metapop_mobility_path is not None:
            mobility_path = metapop_mobility_path
        else:
            env_m = os.environ.get("EOSP_METAPOP_MOBILITY", "")
            mobility_path = Path(env_m) if env_m else _DEFAULT_METAPOP_MOBILITY
        n_runs = metapop_n_runs if metapop_n_runs is not None else int(os.environ.get("EOSP_METAPOP_RUNS", "64"))
        mass = ship_outbreak_mass if ship_outbreak_mass is not None else 100.0
        schedule = load_mobility_schedule(mobility_path)
        init_s, init_e, init_i, init_r = build_initial_metapop_state(
            schedule=schedule, network_spec=spec, ship_outbreak_mass=mass
        )
        params = MetapopParams(
            beta_local=max(0.02, min(0.95, p_transmit * 8.0)),
            sigma=0.35,
            gamma=0.2,
            travel_frac_exposed=1.0,
            travel_frac_infectious=1.0,
        )
        summary = run_ensemble_metapop(
            schedule=schedule,
            params=params,
            init_s=init_s,
            init_e=init_e,
            init_i=init_i,
            init_r=init_r,
            n_runs=n_runs,
            rng_seed=int(os.environ.get("EOSP_METAPOP_SEED", "20260507")),
            progress_callback=metapop_progress_callback,
        )
        sidecar = load_mobility_sidecar_meta(mobility_path)
        heatmap: list[dict[str, Any]] = []
        for entry in summary["patches"]:
            code = str(entry["code"])
            if code in ("NSEED", "SHIP"):
                lat_v, lng_v = _SHIP_LAT, _SHIP_LNG
                city_v, country_v = "", ""
            else:
                ac = coords.get(code)
                if ac is None:
                    continue
                lat_v = float(ac["lat"])
                lng_v = float(ac["lng"])
                city_v = str(ac.get("city", ""))
                country_v = str(ac.get("country", ""))
            med_i = entry["i_median_by_day"]
            risk_score = float(med_i[-1]) if med_i else 0.0
            heatmap.append({
                "airport_iata": code,
                "lat": lat_v,
                "lng": lng_v,
                "city": city_v,
                "country": country_v,
                "risk_score": risk_score,
                "ring": 0,
            })
        primary_layer_id = "metapop_infectious_median_final"
        layers = [
            _heatmap_layer_from_rows(
                layer_id=primary_layer_id,
                label="Metapop median infectious (final day)",
                metric_id="metapop_infectious",
                metric_day="final",
                metric_stat="median",
                rows=heatmap,
            ),
        ]
        provenance = {
            "risk_source": "metapop_monte_carlo",
            "primary_metric_id": primary_layer_id,
            "risk_heatmap_explanation": (
                "Metapop Monte Carlo kernel: ensemble median infectious compartment count per patch on the last "
                "simulated day, driven by the mobility schedule and SEIR local dynamics. Not an OpenSky ring score "
                "and not a calibrated case forecast per airport."
            ),
            "distinct_from": "OpenSky ring heat and ABM geo buckets use different movement and kernel assumptions.",
        }
        sim_extra.update(
            {
                "primary_layer_id": primary_layer_id,
                "layers": layers,
                "p_transmit_used": round(p_transmit, 4),
                "ring1_airports": [],
                "ring2_airports_found": 0,
                "ring3_airports_found": 0,
                "metapop_n_runs": n_runs,
                "metapop_mobility_path": str(mobility_path),
            }
        )
        if sidecar is not None:
            if "mobility_source" in sidecar:
                sim_extra["mobility_source"] = sidecar["mobility_source"]
            if "mobility_license_note" in sidecar:
                sim_extra["mobility_license_note"] = sidecar["mobility_license_note"]
            if "horizon_days" in sidecar:
                sim_extra["mobility_horizon_days"] = sidecar["horizon_days"]
            sim_extra["mobility_bundle_version"] = mobility_path.name
    else:
        heatmap, meta = _resolve_legacy_opensky_heatmap(p_transmit, legacy_opensky_geo)
        primary_layer_id = "legacy_opensky_primary"
        layers = [
            _heatmap_layer_from_rows(
                layer_id=primary_layer_id,
                label="OpenSky flight-ring heuristic",
                metric_id="legacy_opensky",
                metric_day="n/a",
                metric_stat="n/a",
                rows=heatmap,
            ),
        ]
        provenance = {
            "risk_source": str(meta.get("risk_source", "legacy_opensky_fallback")),
            "primary_metric_id": primary_layer_id,
            "risk_heatmap_explanation": str(meta["risk_heatmap_explanation"]),
            "distinct_from": (
                "ABM geo forecast uses ensemble bucket percentiles from the agent-based model; this layer does not."
            ),
        }
        sim_extra.update(
            {
                "primary_layer_id": primary_layer_id,
                "layers": layers,
                "p_transmit_used": meta.get("p_transmit_used", round(p_transmit, 4)),
                "ring1_airports": meta.get("ring1_airports", []),
                "ring2_airports_found": meta.get("ring2_airports_found", 0),
                "ring3_airports_found": meta.get("ring3_airports_found", 0),
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
        "abm_geo_fallback_reason",
        "metapop_n_runs",
        "metapop_mobility_path",
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


def _risk_zones_to_heatmap_rows(zones: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "airport_iata": z.airport_iata,
            "lat": z.lat,
            "lng": z.lng,
            "city": z.city,
            "country": z.country,
            "risk_score": z.risk_score,
            "ring": z.ring,
        }
        for z in zones
    ]


def _legacy_risk_heatmap_and_metadata(p_transmit: float) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Full Rings 1–3 using live OpenSky (or cache). Call only from forecast/simulation, not page loads."""

    risk_zones = compute_risk_zones(p_transmit)
    heatmap = _risk_zones_to_heatmap_rows(risk_zones)
    metadata: dict[str, Any] = {
        "p_transmit_used": round(p_transmit, 4),
        "ring1_airports": [z.airport_iata for z in risk_zones if z.ring == 1],
        "ring2_airports_found": sum(1 for z in risk_zones if z.ring == 2),
        "ring3_airports_found": sum(1 for z in risk_zones if z.ring == 3),
        "risk_heatmap_explanation": (
            "OpenSky flight-ring heuristic: relative connectivity-weighted hazard from evacuation hubs "
            "and onward flights, scaled by inferred p_transmit. Not a count of predicted cases per airport."
        ),
    }
    return heatmap, metadata


def _resolve_legacy_opensky_heatmap(
    p_transmit: float,
    legacy_opensky_geo: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Serve cached full rings from the last baseline simulation, or Ring-1-only without OpenSky."""

    if legacy_opensky_geo and isinstance(legacy_opensky_geo.get("risk_heatmap"), list):
        stored_meta = legacy_opensky_geo.get("metadata")
        meta: dict[str, Any] = dict(stored_meta) if isinstance(stored_meta, dict) else {}
        meta.setdefault("risk_source", "legacy_opensky_cached_simulation")
        return list(legacy_opensky_geo["risk_heatmap"]), meta
    zones = compute_ring1_zones_only(p_transmit)
    heatmap = _risk_zones_to_heatmap_rows(zones)
    return heatmap, {
        "p_transmit_used": round(p_transmit, 4),
        "ring1_airports": [z.airport_iata for z in zones],
        "ring2_airports_found": 0,
        "ring3_airports_found": 0,
        "risk_source": "legacy_opensky_ring1_only",
        "risk_heatmap_explanation": (
            "Evacuation hubs only (Ring 1). Full OpenSky flight rings (Rings 2–3) are computed when you "
            "run a baseline forecast and stored with the simulation output — not on each page load."
        ),
    }


def build_legacy_opensky_geo_snapshot(p_transmit: float) -> dict[str, Any]:
    """Persist with baseline ``ForecastResponse`` so map reads avoid live OpenSky calls."""

    heatmap, meta = _legacy_risk_heatmap_and_metadata(p_transmit)
    meta = dict(meta)
    meta["risk_source"] = "legacy_opensky_live_simulation"
    return {"risk_heatmap": heatmap, "metadata": meta}


def build_outbreak_geo(
    *,
    p_transmit: float,
    cases: list[CaseRecord],
    risk_model: str = "legacy",
    metapop_n_runs: int | None = None,
    metapop_mobility_path: Path | None = None,
    ship_outbreak_mass: float | None = None,
    metapop_progress_callback: Callable[[dict[str, Any]], None] | None = None,
    abm_geo_forecast: dict[str, Any] | None = None,
    legacy_opensky_geo: dict[str, Any] | None = None,
    inference_version: str | None = None,
    forecast_n_simulations: int | None = None,
    ensemble_spec_hash: str | None = None,
    scenario: str = "baseline",
) -> dict[str, Any]:
    """Build the complete geo/outbreak payload.

    ``p_transmit`` should come from the latest inference when available; callers
    pass a neutral default (e.g. prior mean) when there is no posterior yet.
    Map markers reflect ``cases`` only.

    Unknown ``risk_model`` values are treated as ``"legacy"``.
    """

    bundle = build_geo_bundle(
        p_transmit=p_transmit,
        cases=cases,
        risk_model=risk_model,
        metapop_n_runs=metapop_n_runs,
        metapop_mobility_path=metapop_mobility_path,
        ship_outbreak_mass=ship_outbreak_mass,
        metapop_progress_callback=metapop_progress_callback,
        abm_geo_forecast=abm_geo_forecast,
        legacy_opensky_geo=legacy_opensky_geo,
        inference_version=inference_version,
        scenario=scenario,
        forecast_n_simulations=forecast_n_simulations,
        ensemble_spec_hash=ensemble_spec_hash,
    )
    return outbreak_dict_from_geo_bundle(bundle)


def _load_spec() -> dict[str, Any]:
    with _SPEC_PATH.open() as fh:
        return json.load(fh)
