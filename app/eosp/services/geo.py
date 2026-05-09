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
    """Build heatmap rows from ABM geo forecast bucket stats on the final forecast day."""

    by_day = abm_geo_forecast["by_day"]
    last = by_day[-1]
    buckets = last.get("buckets") or {}
    rows: list[dict[str, Any]] = []
    for code, stats in buckets.items():
        if code == "ship":
            continue
        if not isinstance(stats, dict):
            continue
        block = stats.get("infectious_I")
        if block is None:
            if "median" in stats and not any(isinstance(v, dict) for v in stats.values()):
                block = stats
        median_i = float(block["median"]) if block and "median" in block else 0.0
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
            "risk_score": median_i,
            "ring": 0,
        })
    return rows


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
) -> dict[str, Any]:
    """Build the complete geo/outbreak payload.

    ``p_transmit`` should come from the latest inference when available; callers
    pass a neutral default (e.g. prior mean) when there is no posterior yet.
    Map markers reflect ``cases`` only.

    Unknown ``risk_model`` values are treated as ``"legacy"``.
    """

    spec = _load_spec()
    coords = load_airport_coords()

    ship = {
        "lat": _SHIP_LAT,
        "lng": _SHIP_LNG,
        "name": spec.get("ship", {}).get("name", "MV Hondius"),
        "status": "en_route_tenerife",
    }

    confirmed_cases = _case_markers_from_records(cases, coords)

    evacuation_flights = []
    for flight in spec.get("flights", []):
        dest_code = flight.get("destination", "")  # e.g. "ZA_JNB"
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

    if rm == "abm_geo":
        by_day = (abm_geo_forecast or {}).get("by_day") or []
        if not by_day:
            heatmap, meta = _resolve_legacy_opensky_heatmap(p_transmit, legacy_opensky_geo)
            base_expl = str(meta["risk_heatmap_explanation"])
            meta["risk_source"] = "legacy_opensky_fallback"
            meta["abm_geo_fallback_reason"] = "no_cached_baseline_geo_forecast"
            meta["risk_heatmap_explanation"] = (
                base_expl
                + " Fallback: ABM geo forecast was unavailable; showing cached flight rings or hub-only heatmap."
            )
            return {
                "ship": ship,
                "confirmed_cases": confirmed_cases,
                "evacuation_flights": evacuation_flights,
                "risk_heatmap": heatmap,
                "metadata": meta,
            }
        heatmap = _risk_heatmap_rows_from_abm_geo_forecast(abm_geo_forecast, coords)
        return {
            "ship": ship,
            "confirmed_cases": confirmed_cases,
            "evacuation_flights": evacuation_flights,
            "risk_heatmap": heatmap,
            "metadata": {
                "p_transmit_used": round(p_transmit, 4),
                "ring1_airports": [],
                "ring2_airports_found": 0,
                "ring3_airports_found": 0,
                "risk_source": "abm_geo_forecast",
                "risk_metric_id": "infectious_present_median_final_day",
                "risk_heatmap_explanation": (
                    "ABM ensemble median count of infectious (I) agents per destination bucket on the final "
                    "forecast day — model-structured, not reported incidence."
                ),
            },
        }

    if rm != "metapop":
        heatmap, meta = _resolve_legacy_opensky_heatmap(p_transmit, legacy_opensky_geo)
        return {
            "ship": ship,
            "confirmed_cases": confirmed_cases,
            "evacuation_flights": evacuation_flights,
            "risk_heatmap": heatmap,
            "metadata": meta,
        }

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
    extra_meta: dict[str, Any] = {}
    if sidecar is not None:
        extra_meta["mobility_bundle_version"] = mobility_path.name
        if "mobility_source" in sidecar:
            extra_meta["mobility_source"] = sidecar["mobility_source"]
        if "mobility_license_note" in sidecar:
            extra_meta["mobility_license_note"] = sidecar["mobility_license_note"]
        if "horizon_days" in sidecar:
            extra_meta["mobility_horizon_days"] = sidecar["horizon_days"]

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

    return {
        "ship": ship,
        "confirmed_cases": confirmed_cases,
        "evacuation_flights": evacuation_flights,
        "risk_heatmap": heatmap,
        "metadata": {
            "p_transmit_used": round(p_transmit, 4),
            "ring1_airports": [],
            "ring2_airports_found": 0,
            "ring3_airports_found": 0,
            "risk_source": "metapop_monte_carlo",
            "metapop_n_runs": n_runs,
            "metapop_mobility_path": str(mobility_path),
            "risk_heatmap_explanation": (
                "Metapop Monte Carlo kernel: ensemble median infectious compartment count per patch on the last "
                "simulated day, driven by the mobility schedule and SEIR local dynamics. Not an OpenSky ring score "
                "and not a calibrated case forecast per airport."
            ),
            **extra_meta,
        },
    }


def _load_spec() -> dict[str, Any]:
    with _SPEC_PATH.open() as fh:
        return json.load(fh)
