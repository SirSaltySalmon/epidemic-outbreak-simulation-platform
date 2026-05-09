/**
 * Leaflet world map: ship marker, case markers, evacuation flight arcs,
 * global risk heatmap from GET /api/v1/geo/outbreak.
 */

let _map = null;
/** Always visible: ship, evacuation arcs. */
let _mapBaseGroup = null;
/** Toggle: recorded case markers. */
let _mapRecordedGroup = null;
/** Toggle: forecast heat, popup targets, green ABM circles. */
let _mapForecastGroup = null;
let _mapLegendEl = null;
let _layerTogglesWired = false;

/** Risk layer from cached ABM ``geo_forecast`` on the baseline forecast. */
const ABM_GEO_RISK_SOURCE = "abm_geo_forecast";
/** Placeholder until a baseline run caches ``geo_forecast``. */
const ABM_GEO_UNAVAILABLE_SOURCE = "abm_geo_unavailable";

function _riskSource(data) {
  if (data && data.schema_version && data.provenance) {
    return data.provenance.risk_source;
  }
  return data?.metadata?.risk_source;
}

function _isAbmGeoKernel(data) {
  const rs = _riskSource(data);
  return rs === ABM_GEO_RISK_SOURCE || rs === ABM_GEO_UNAVAILABLE_SOURCE;
}

/** Square-root normalize count-style heat values for leaflet.heat (ABM bucket medians). */
function _normalizeForHeat(features) {
  const filtered = features.filter((f) => (Number(f.value ?? f.risk_score) || 0) > 0);
  if (!filtered.length) return [];
  const vals = filtered.map((f) => Number(f.value ?? f.risk_score) || 0);
  const maxVal = Math.max(...vals, 1);
  return filtered.map((f, i) => {
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
      mobility_bundle_version: sim.mobility_bundle_version,
      mobility_source: sim.mobility_source,
      mobility_license_note: sim.mobility_license_note,
      mobility_horizon_days: sim.mobility_horizon_days,
      risk_metric_id: sim.risk_metric_id,
    },
  };
}

function _ensureLayerGroups() {
  if (_mapBaseGroup) return;
  _mapBaseGroup = L.layerGroup().addTo(_map);
  _mapRecordedGroup = L.layerGroup();
  _mapForecastGroup = L.layerGroup();
  _syncMapLayerVisibility();
}

function _syncMapLayerVisibility() {
  if (!_map || !_mapRecordedGroup) return;
  const recEl = document.getElementById("map-layer-recorded");
  const foreEl = document.getElementById("map-layer-forecast");
  const showRec = recEl ? recEl.checked : true;
  const showFore = foreEl ? foreEl.checked : true;
  if (showRec) _mapRecordedGroup.addTo(_map);
  else _mapRecordedGroup.remove();
  if (showFore) _mapForecastGroup.addTo(_map);
  else _mapForecastGroup.remove();
}

function _wireLayerToggles() {
  if (_layerTogglesWired) return;
  const rec = document.getElementById("map-layer-recorded");
  const fore = document.getElementById("map-layer-forecast");
  if (!rec || !fore) return;
  _layerTogglesWired = true;
  const onChange = () => _syncMapLayerVisibility();
  rec.addEventListener("change", onChange);
  fore.addEventListener("change", onChange);
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
  _ensureLayerGroups();
  _wireLayerToggles();
  return _map;
}

export function renderGeoData(data, geoForecast) {
  if (!_map) return;

  if (data && data.schema_version) {
    data = _legacyShapeFromBundle(data);
  }

  _ensureLayerGroups();
  _wireLayerToggles();

  _mapBaseGroup.clearLayers();
  _mapRecordedGroup.clearLayers();
  _mapForecastGroup.clearLayers();

  // Ship marker — pulsing circle
  const shipIcon = L.divIcon({
    className: "",
    html: `<div style="width:14px;height:14px;border-radius:50%;background:#0b7c83;
           border:2px solid #9ccac6;box-shadow:0 0 0 4px rgba(11,124,131,0.3)"></div>`,
    iconSize: [14, 14],
    iconAnchor: [7, 7],
  });
  const shipMarker = L.marker([data.ship.lat, data.ship.lng], { icon: shipIcon })
    .bindPopup(`<strong>${data.ship.name}</strong><br>Status: ${data.ship.status.replace(/_/g, " ")}`);
  _mapBaseGroup.addLayer(shipMarker);

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
      `Confirmed: ${c.confirmed} · Suspected: ${c.suspected} · Deaths: ${c.deaths}`,
    );
    _mapRecordedGroup.addLayer(m);
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
      `${flight.passengers} passengers · Day ${flight.depart_day}`,
    );
    _mapBaseGroup.addLayer(line);
  }

  // Risk heatmap (intensity 0–1 for leaflet.heat; sqrt-normalized ABM medians)
  const isAbmGeo = _isAbmGeoKernel(data);
  const heatPoints = isAbmGeo
    ? _normalizeForHeat(data.risk_heatmap)
    : data.risk_heatmap
        .filter((z) => (Number(z.risk_score) || 0) > 0)
        .map((z) => {
          const raw = Number(z.risk_score) || 0;
          return [z.lat, z.lng, raw];
        });
  if (heatPoints.length > 0) {
    const heatLayer = L.heatLayer(heatPoints, {
      radius: isAbmGeo ? 22 : 30,
      blur: isAbmGeo ? 16 : 20,
      max: 1.0,
      maxZoom: 6,
      gradient: { 0.2: "#d89c22", 0.5: "#b95b35", 0.8: "#8b0000", 1.0: "#ff0000" },
    });
    _mapForecastGroup.addLayer(heatLayer);
  }

  if (isAbmGeo && _riskSource(data) === ABM_GEO_RISK_SOURCE) {
    const ranked = [...data.risk_heatmap]
      .filter((z) => (Number(z.risk_score) || 0) > 0)
      .sort((a, b) => (Number(b.risk_score) || 0) - (Number(a.risk_score) || 0));
    for (const z of ranked) {
      const raw = Number(z.risk_score) || 0;
      const label = z.city ? `${z.city} (${z.airport_iata})` : z.airport_iata;
      const countryLine = z.country ? `${z.country}<br>` : "";
      const layerCopy =
        `ABM geo heat: median <strong>cumulative infections</strong> on final forecast day ≈ <strong>${raw.toFixed(2)}</strong>.<br>` +
        `<em>Forecast bucket median — not a live flight tracker.</em>`;
      const m = L.circleMarker([z.lat, z.lng], {
        radius: 8,
        fillColor: "transparent",
        color: "transparent",
        interactive: true,
      }).bindPopup(
        `<strong>${label}</strong><br>` +
        `${countryLine}` +
        layerCopy,
      );
      _mapForecastGroup.addLayer(m);
    }
  }

  _renderAbmSimulationLayer(data, geoForecast);
  _syncMapLayerVisibility();
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

/** Forecast (green) ring radii — sqrt-scaled vs max bucket so sizes sit nearer recorded-case markers (5+3×cases). */
const _ABM_GREEN_R_MIN = 3;
const _ABM_GREEN_R_SPAN = 9;

function _renderAbmSimulationLayer(data, geoForecast) {
  if (!geoForecast || !geoForecast.by_day || !geoForecast.by_day.length) return;
  const lastDay = geoForecast.by_day[geoForecast.by_day.length - 1];
  const entries = Object.entries(lastDay.buckets).filter(([k]) => k !== "ship");
  const positiveMedians = entries
    .map(([, v]) => _abmBucketCumulativeBlock(v))
    .filter((b) => b != null && Number(b.median) > 0)
    .map((b) => b.median);
  const mx = Math.max(...positiveMedians, 1);
  for (const [code, stats] of entries) {
    const block = _abmBucketCumulativeBlock(stats);
    if (!block || Number(block.median) <= 0) continue;
    const iata = code.includes("_") ? code.split("_").pop() : code;
    const ll = _airportLatLng(data, iata);
    if (!ll) continue;
    const norm = Math.min(1, Math.max(0, Number(block.median) / mx));
    const t = Math.sqrt(norm);
    const r = Math.max(
      _ABM_GREEN_R_MIN,
      Math.round(_ABM_GREEN_R_MIN + t * _ABM_GREEN_R_SPAN),
    );
    const m = L.circleMarker(ll, {
      radius: r,
      color: "#69f0ae",
      fillColor: "#69f0ae",
      weight: 1,
      fillOpacity: 0.45,
    }).bindPopup(
      `<strong>Simulation · ${iata} cluster</strong><br>` +
      `Median cumulative infected in bucket (E+I+R+D), day ${lastDay.day}: <strong>${block.median}</strong><br>` +
      `95% CI: ${block.ci_95_lower}–${block.ci_95_upper}<br>` +
      `<em>From ship-network ABM ensemble — independent of the orange heat layer.</em>`,
    );
    _mapForecastGroup.addLayer(m);
  }
}

function _updateMapLegend(data, geoForecast) {
  if (!_mapLegendEl) _mapLegendEl = document.getElementById("map-legend");
  if (!_mapLegendEl) return;
  const heatNote =
    data.metadata?.risk_heatmap_explanation ||
    "Orange-red heat: ABM destination bucket medians when a baseline forecast is cached.";
  const abmGeoHint =
    _riskSource(data) === ABM_GEO_RISK_SOURCE
      ? " Orange heat is ABM cumulative-infection median on the final forecast day."
      : "";
  const abmNote = geoForecast
    ? "Green circles: ABM median cumulative infected (E+I+R+D) at destination clusters, day 14 — radius is sqrt-scaled to the busiest cluster so sizes stay near recorded-case markers."
    : "Green circles appear after a full forecast run caches geo bucket outputs.";
  _mapLegendEl.innerHTML =
    `<strong>Map layers</strong> — ${heatNote}${abmGeoHint} <strong>|</strong> ${abmNote} ` +
    `<strong>|</strong> Use the toggles above to show or hide recorded cases vs forecast.`;
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
