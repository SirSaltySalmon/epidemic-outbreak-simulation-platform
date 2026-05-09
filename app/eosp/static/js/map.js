/**
 * Leaflet world map: ship marker, case markers, evacuation flight arcs,
 * global risk heatmap from GET /api/v1/geo/outbreak.
 */

let _map = null;
let _heatLayer = null;
let _caseMarkers = [];
let _arcLines = [];
let _abmMarkers = [];
/** Transparent click targets for risk heatmap airports (must be cleared on re-render). */
let _riskPopupMarkers = [];
let _mapLegendEl = null;

/** Metapop kernel uses infectious medians; legacy uses OpenSky ring scores. */
const METAPOP_RISK_SOURCE = "metapop_monte_carlo";
const ABM_GEO_RISK_SOURCE = "abm_geo_forecast";
/** Max click-to-popup markers for metapop (full graph has 1000+ airports). */
const METAPOP_POPUP_MARKER_CAP = 120;

function _riskSource(data) {
  if (data && data.schema_version && data.provenance) {
    return data.provenance.risk_source;
  }
  return data?.metadata?.risk_source;
}

function _isMetapopKernel(data) {
  return _riskSource(data) === METAPOP_RISK_SOURCE;
}

function _isAbmGeoKernel(data) {
  return _riskSource(data) === ABM_GEO_RISK_SOURCE;
}

/** Square-root normalize count-style heat values for leaflet.heat (matches metapop scaling). */
function _normalizeForHeat(features) {
  const vals = features.map((f) => Number(f.value ?? f.risk_score) || 0);
  const maxVal = Math.max(...vals, 1);
  return features.map((f, i) => {
    const lat = f.lat ?? f[0];
    const lng = f.lng ?? f[1];
    const raw = vals[i];
    return [lat, lng, Math.sqrt(raw / maxVal)];
  });
}

function _legacyShapeFromBundle(bundle) {
  const sim = bundle.simulation;
  const primary = sim.layers.find((l) => l.id === sim.primary_layer_id);
  const feats = primary ? primary.features : [];
  const risk_heatmap = feats.map((f) => ({
    airport_iata: f.airport_iata,
    lat: f.lat,
    lng: f.lng,
    city: f.city,
    country: f.country,
    risk_score: f.value,
    ring: f.ring ?? 0,
  }));
  return {
    ship: bundle.ship,
    confirmed_cases: bundle.observed.case_markers,
    evacuation_flights: bundle.evacuation_flights,
    risk_heatmap,
    metadata: {
      ...bundle.provenance,
      n_simulations: sim.n_simulations,
      ensemble_spec_hash: sim.ensemble_spec_hash,
      inference_version: sim.inference_version,
      scenario: sim.scenario,
      primary_layer_id: sim.primary_layer_id,
      p_transmit_used: sim.p_transmit_used,
      ring1_airports: sim.ring1_airports,
      ring2_airports_found: sim.ring2_airports_found,
      ring3_airports_found: sim.ring3_airports_found,
      abm_geo_fallback_reason: sim.abm_geo_fallback_reason,
      metapop_n_runs: sim.metapop_n_runs,
      metapop_mobility_path: sim.metapop_mobility_path,
      mobility_bundle_version: sim.mobility_bundle_version,
      mobility_source: sim.mobility_source,
      mobility_license_note: sim.mobility_license_note,
      mobility_horizon_days: sim.mobility_horizon_days,
      risk_metric_id: sim.risk_metric_id,
    },
  };
}

export function initMap(containerId) {
  if (_map) return _map;
  _map = L.map(containerId, {
    center: [20, 10],
    zoom: 2,
    zoomControl: true,
    attributionControl: true,
  });
  L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
    attribution: '&copy; <a href="https://carto.com/">CARTO</a>',
    subdomains: "abcd",
    maxZoom: 19,
  }).addTo(_map);
  return _map;
}

export function renderGeoData(data, geoForecast) {
  if (!_map) return;

  if (data && data.schema_version) {
    data = _legacyShapeFromBundle(data);
  }

  // Clear previous layers
  _caseMarkers.forEach((m) => m.remove());
  _caseMarkers = [];
  _arcLines.forEach((l) => l.remove());
  _arcLines = [];
  _abmMarkers.forEach((m) => m.remove());
  _abmMarkers = [];
  _riskPopupMarkers.forEach((m) => m.remove());
  _riskPopupMarkers = [];
  if (_heatLayer) { _heatLayer.remove(); _heatLayer = null; }

  // Ship marker — pulsing circle
  const shipIcon = L.divIcon({
    className: "",
    html: `<div style="width:14px;height:14px;border-radius:50%;background:#0b7c83;
           border:2px solid #9ccac6;box-shadow:0 0 0 4px rgba(11,124,131,0.3)"></div>`,
    iconSize: [14, 14],
    iconAnchor: [7, 7],
  });
  const shipMarker = L.marker([data.ship.lat, data.ship.lng], { icon: shipIcon })
    .bindPopup(`<strong>${data.ship.name}</strong><br>Status: ${data.ship.status.replace(/_/g, " ")}`)
    .addTo(_map);
  _caseMarkers.push(shipMarker);

  // Confirmed/suspected case markers
  for (const c of data.confirmed_cases) {
    const total = c.confirmed + c.suspected;
    const radius = 5 + total * 3;
    const color = c.confirmed > 0 ? "#b95b35" : "#d89c22";
    const m = L.circleMarker([c.lat, c.lng], {
      radius,
      fillColor: color,
      color: color,
      weight: 1,
      fillOpacity: 0.75,
    }).bindPopup(
      `<strong>${c.country}</strong><br>` +
      `Confirmed: ${c.confirmed} · Suspected: ${c.suspected} · Deaths: ${c.deaths}`
    ).addTo(_map);
    _caseMarkers.push(m);
  }

  // Evacuation flight arcs (curved polyline approximation)
  for (const flight of data.evacuation_flights) {
    const arc = _curvedLine(
      [flight.from_lat, flight.from_lng],
      [flight.to_lat, flight.to_lng],
    );
    const line = L.polyline(arc, {
      color: "#9ccac6",
      weight: 1.5,
      opacity: 0.6,
      dashArray: "6 4",
    }).bindPopup(
      `<strong>${flight.name}</strong><br>` +
      `${flight.passengers} passengers · Day ${flight.depart_day}`
    ).addTo(_map);
    _arcLines.push(line);
  }

  // Risk heatmap (intensity 0–1 for leaflet.heat; count kernels are normalized)
  const isMetapop = _isMetapopKernel(data);
  const isAbmGeo = _isAbmGeoKernel(data);
  const isCountKernel = isMetapop || isAbmGeo;
  const heatPoints = isCountKernel
    ? _normalizeForHeat(data.risk_heatmap)
    : data.risk_heatmap.map((z) => {
        const raw = Number(z.risk_score) || 0;
        return [z.lat, z.lng, raw];
      });
  _heatLayer = L.heatLayer(heatPoints, {
    radius: isCountKernel ? 22 : 30,
    blur: isCountKernel ? 16 : 20,
    max: 1.0,
    maxZoom: 6,
    gradient: { 0.2: "#d89c22", 0.5: "#b95b35", 0.8: "#8b0000", 1.0: "#ff0000" },
  }).addTo(_map);

  // Popup targets for heat airports (transparent markers)
  if (isCountKernel) {
    const ranked = [...data.risk_heatmap]
      .filter((z) => (Number(z.risk_score) || 0) > 0)
      .sort((a, b) => (Number(b.risk_score) || 0) - (Number(a.risk_score) || 0));
    const top = isMetapop ? ranked.slice(0, METAPOP_POPUP_MARKER_CAP) : ranked;
    for (const z of top) {
      const raw = Number(z.risk_score) || 0;
      const label = z.city ? `${z.city} (${z.airport_iata})` : z.airport_iata;
      const countryLine = z.country ? `${z.country}<br>` : "";
      const layerCopy = isMetapop
        ? `Metapop layer: median <strong>infectious (I)</strong> on last sim day ≈ <strong>${raw.toFixed(2)}</strong> (ensemble).<br>` +
          `<em>Scheduled-connectivity proxy — not reported case counts.</em>`
        : `ABM geo heat: median <strong>cumulative infections</strong> on final forecast day ≈ <strong>${raw.toFixed(2)}</strong>.<br>` +
          `<em>Forecast bucket median — not OpenSky ring scores.</em>`;
      const m = L.circleMarker([z.lat, z.lng], {
        radius: 8,
        fillColor: "transparent",
        color: "transparent",
        interactive: true,
      }).bindPopup(
        `<strong>${label}</strong><br>` +
        `${countryLine}` +
        layerCopy
      ).addTo(_map);
      _riskPopupMarkers.push(m);
    }
  } else {
    for (const z of data.risk_heatmap) {
      if (z.ring === 1) continue;
      const level = z.risk_score > 0.5 ? "high" : z.risk_score > 0.2 ? "medium" : "low";
      const m = L.circleMarker([z.lat, z.lng], {
        radius: 8,
        fillColor: "transparent",
        color: "transparent",
      }).bindPopup(
        `<strong>${z.city} (${z.airport_iata})</strong><br>` +
        `OpenSky ring score: ${level} (relative hazard, not case counts)<br>` +
        `<em>Ring ${z.ring} — see map legend for simulation overlay</em>`
      ).addTo(_map);
      _riskPopupMarkers.push(m);
    }
  }

  _renderAbmSimulationLayer(data, geoForecast);
  _updateMapLegend(data, geoForecast);
}

function _airportLatLng(geoData, iata) {
  const z = geoData.risk_heatmap.find((x) => x.airport_iata === iata);
  if (z) return [z.lat, z.lng];
  const c = geoData.confirmed_cases.find((x) => x.airport === iata);
  if (c) return [c.lat, c.lng];
  return null;
}

function _abmBucketCumulativeBlock(stats) {
  if (!stats) return null;
  if (typeof stats.median === "number") return stats;
  if (stats.cumulative_infected) return stats.cumulative_infected;
  return null;
}

function _renderAbmSimulationLayer(data, geoForecast) {
  if (!geoForecast || !geoForecast.by_day || !geoForecast.by_day.length) return;
  const lastDay = geoForecast.by_day[geoForecast.by_day.length - 1];
  const entries = Object.entries(lastDay.buckets).filter(([k]) => k !== "ship");
  const medians = entries
    .map(([, v]) => _abmBucketCumulativeBlock(v))
    .filter((b) => b != null)
    .map((b) => b.median);
  const mx = Math.max(...medians, 1);
  for (const [code, stats] of entries) {
    const block = _abmBucketCumulativeBlock(stats);
    if (!block) continue;
    const iata = code.includes("_") ? code.split("_").pop() : code;
    const ll = _airportLatLng(data, iata);
    if (!ll) continue;
    const r = 6 + Math.round((block.median / mx) * 22);
    const m = L.circleMarker(ll, {
      radius: r,
      color: "#69f0ae",
      fillColor: "#69f0ae",
      weight: 1,
      fillOpacity: 0.45,
    }    ).bindPopup(
      `<strong>Simulation · ${iata} cluster</strong><br>` +
      `Median cumulative infected in bucket (E+I+R+D), day ${lastDay.day}: <strong>${block.median}</strong><br>` +
      `95% CI: ${block.ci_95_lower}–${block.ci_95_upper}<br>` +
      `<em>From ship-network ABM ensemble — independent of the orange heat layer.</em>`
    ).addTo(_map);
    _abmMarkers.push(m);
  }
}

function _updateMapLegend(data, geoForecast) {
  if (!_mapLegendEl) _mapLegendEl = document.getElementById("map-legend");
  if (!_mapLegendEl) return;
  const heatNote =
    data.metadata?.risk_heatmap_explanation ||
    "Orange-red heat: flight-network heuristic (OpenSky rings × p_transmit), not literal case forecasts.";
  const metapopHint = _isMetapopKernel(data)
    ? ` Popups on the top ${METAPOP_POPUP_MARKER_CAP} airports by median I (zoom heat for the rest).`
    : "";
  const abmGeoHint = _isAbmGeoKernel(data)
    ? " Orange heat is ABM cumulative-infection median on the final forecast day."
    : "";
  const abmNote = geoForecast
    ? "Green circles: ABM median cumulative infected (E+I+R+D) at destination clusters, day 14 — width scales to the busiest cluster in this run."
    : "Green circles appear after a full forecast run caches geo bucket outputs.";
  _mapLegendEl.innerHTML =
    `<strong>Map layers</strong> — ${heatNote}${metapopHint}${abmGeoHint} <strong>|</strong> ${abmNote}`;
}

/** Build ~20 intermediate points along a great-circle-ish curve */
function _curvedLine(from, to) {
  const points = [];
  const steps = 20;
  for (let i = 0; i <= steps; i++) {
    const t = i / steps;
    const lat = from[0] + (to[0] - from[0]) * t;
    const lng = from[1] + (to[1] - from[1]) * t;
    // Add a slight arc by offsetting the midpoint north
    const arc = Math.sin(Math.PI * t) * Math.abs(to[0] - from[0]) * 0.3;
    points.push([lat + arc, lng]);
  }
  return points;
}
