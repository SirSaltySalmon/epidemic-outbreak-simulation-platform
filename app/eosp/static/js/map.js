/**
 * Leaflet world map: ship marker, case markers, evacuation flight arcs,
 * global risk heatmap from GET /api/v1/geo/outbreak; optional replay from replay_geo.
 */

import { get } from "./api.js";

let _map = null;
/** Always visible: ship, evacuation arcs. */
let _mapBaseGroup = null;
/** Toggle: recorded case markers. */
let _mapRecordedGroup = null;
/** Toggle: forecast heat, popups, green simulation circles. */
let _mapForecastGroup = null;
let _mapForecastHeatSubgroup = null;
let _mapForecastGreenSubgroup = null;
let _heatLayerInstance = null;
let _mapLegendEl = null;
let _layerTogglesWired = false;

/** Last render payload for replay slider (no PHI). */
let _lastGeoData = null;
let _lastGeoForecast = null;
let _lastReplayGeo = null;
let _replayDayIndex = 0;
let _replayMetric = "C";
/** IATA upper -> {lat,lng} from bundled reference/airports once loaded. */
let _replayRefCoordsPromise = null;
let _replayControlsWired = false;

/** API risk_source for hub-timeline cached geo (backward-compatible name). */
const ABM_GEO_RISK_SOURCE = "abm_geo_forecast";
const ABM_GEO_UNAVAILABLE_SOURCE = "abm_geo_unavailable";

function _riskSource(data) {
  if (data && data.schema_version && data.provenance) {
    return data.provenance.risk_source;
  }
  return data?.metadata?.risk_source;
}

function _isForecastGeoRiskKernel(data) {
  const rs = _riskSource(data);
  return rs === ABM_GEO_RISK_SOURCE || rs === ABM_GEO_UNAVAILABLE_SOURCE;
}

/** Square-root normalize count-style heat values for leaflet.heat. */
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
  _mapForecastHeatSubgroup = L.layerGroup();
  _mapForecastGreenSubgroup = L.layerGroup();
  _mapForecastHeatSubgroup.addTo(_mapForecastGroup);
  _mapForecastGreenSubgroup.addTo(_mapForecastGroup);
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

function _removeHeatLayer() {
  if (_heatLayerInstance && _mapForecastHeatSubgroup) {
    _mapForecastHeatSubgroup.removeLayer(_heatLayerInstance);
  }
  _heatLayerInstance = null;
}

function _applyHeatPoints(heatPoints, isSqrtNormStyle) {
  _removeHeatLayer();
  if (!heatPoints?.length || !_mapForecastHeatSubgroup) return;
  _heatLayerInstance = L.heatLayer(heatPoints, {
    radius: isSqrtNormStyle ? 22 : 30,
    blur: isSqrtNormStyle ? 16 : 20,
    max: 1.0,
    maxZoom: 6,
    gradient: { 0.2: "#d89c22", 0.5: "#b95b35", 0.8: "#8b0000", 1.0: "#ff0000" },
  });
  _mapForecastHeatSubgroup.addLayer(_heatLayerInstance);
}

/**
 * Cached reference airports for replay coords not on risk_heatmap/cases list.
 */
function _warmReplayCoords() {
  if (_replayRefCoordsPromise) return _replayRefCoordsPromise;
  _replayRefCoordsPromise = get("/reference/airports")
    .then((res) => {
      for (const a of res.airports || []) {
        if (a.iata && a.lat != null && a.lng != null) {
          _replayRefLatLng[a.iata.toUpperCase()] = { lat: Number(a.lat), lng: Number(a.lng) };
        }
      }
    })
    .catch(() => {});
  return _replayRefCoordsPromise;
}

/** @type {Record<string, {lat: number, lng: number}>} */
const _replayRefLatLng = {};

function _ensureReplayControlsWired() {
  if (_replayControlsWired) return;
  const slider = document.getElementById("map-replay-day");
  const cRadio = document.getElementById("map-replay-metric-c");
  const aRadio = document.getElementById("map-replay-metric-a");
  if (!slider || !cRadio || !aRadio) return;
  _replayControlsWired = true;
  slider.addEventListener("input", () => {
    _replayDayIndex = Number(slider.value) || 0;
    void _refreshReplayMapOverlays();
  });
  cRadio.addEventListener("change", () => {
    if (cRadio.checked) {
      _replayMetric = "C";
      void _refreshReplayMapOverlays();
    }
  });
  aRadio.addEventListener("change", () => {
    if (aRadio.checked) {
      _replayMetric = "A";
      void _refreshReplayMapOverlays();
    }
  });
}

function _replayDaysList() {
  const rg = _lastReplayGeo;
  return rg && Array.isArray(rg.days) ? rg.days : [];
}

/** Match ``replay_geo.days[i].date`` to ``geo_forecast.by_day[].date``. */
function _byDayRowForCurrentReplayDate() {
  const gf = _lastGeoForecast;
  if (!gf?.by_day?.length) return null;
  const days = _replayDaysList();
  if (!days.length) return gf.by_day[gf.by_day.length - 1];
  const idx = Math.min(Math.max(_replayDayIndex, 0), days.length - 1);
  const wantDate = days[idx]?.date;
  if (wantDate == null) return gf.by_day[gf.by_day.length - 1];
  const w = String(wantDate).slice(0, 10);
  const hit = gf.by_day.find((b) => String(b.date).slice(0, 10) === w);
  return hit || gf.by_day[gf.by_day.length - 1];
}

function _greenLayerSelectedByDay(data) {
  if (!_lastReplayGeo?.days?.length || !_forecastGeoKernel(data)) return null;
  return _byDayRowForCurrentReplayDate();
}

function _paintReplayGreenLayer() {
  if (!_mapForecastGreenSubgroup || !_lastGeoData || !_lastGeoForecast) return;
  const replayOn = !!( _lastReplayGeo?.days?.length && _forecastGeoKernel(_lastGeoData));
  const byDayRow = replayOn ? _greenLayerSelectedByDay(_lastGeoData) : null;
  _renderHubTimelineGreenLayer(_lastGeoData, _lastGeoForecast, byDayRow);
}

async function _refreshReplayMapOverlays() {
  _updateReplayDateLabel();
  await _paintReplayOrangeHeat();
  _paintReplayGreenLayer();
  _updateMapLegend(_lastGeoData, _lastGeoForecast, _lastReplayGeo);
}

function _updateReplayDateLabel() {
  const lbl = document.getElementById("map-replay-date-label");
  const slider = document.getElementById("map-replay-day");
  const days = _replayDaysList();
  if (!lbl || !days.length) return;
  const idx = Math.min(Math.max(_replayDayIndex, 0), days.length - 1);
  lbl.textContent = days[idx]?.date ?? "";
  if (slider) {
    slider.setAttribute("aria-valuetext", days[idx]?.date ?? `${idx}`);
  }
}

async function _paintReplayOrangeHeat() {
  if (!_mapForecastHeatSubgroup || !_lastGeoData) return;
  const days = _replayDaysList();
  if (!days.length || !_forecastGeoKernel(_lastGeoData)) {
    _removeHeatLayer();
    return;
  }
  await _warmReplayCoords();
  const idx = Math.min(Math.max(_replayDayIndex, 0), days.length - 1);
  const dayObj = days[idx];
  const airports = dayObj.airports || {};
  const feats = [];
  for (const [iataUpper, vals] of Object.entries(airports)) {
    const key = String(iataUpper).toUpperCase();
    const raw =
      _replayMetric === "A" ? vals.A_median ?? vals.a_median : vals.C_median ?? vals.c_median;
    if (raw == null || Number(raw) <= 0) continue;
    const ll = _airportLatLng(_lastGeoData, key) || _replayRefLatLng[key];
    if (!ll) continue;
    feats.push({
      airport_iata: key,
      lat: ll.lat ?? ll[0],
      lng: ll.lng ?? ll[1],
      risk_score: Number(raw),
    });
  }
  const heatPts = _normalizeForHeat(feats);
  _applyHeatPoints(heatPts, true);
}

function _forecastGeoKernel(data) {
  return _isForecastGeoRiskKernel(data) && _riskSource(data) === ABM_GEO_RISK_SOURCE;
}

async function _setupReplayUi(replayGeo) {
  _lastReplayGeo = replayGeo && replayGeo.days?.length ? replayGeo : null;
  const wrap = document.getElementById("map-replay-controls");
  const slider = document.getElementById("map-replay-day");
  const days = _replayDaysList();
  if (!_lastGeoData || !wrap || !slider) return;
  if (!days.length || !_forecastGeoKernel(_lastGeoData)) {
    wrap.hidden = true;
    return;
  }
  wrap.hidden = false;
  _ensureReplayControlsWired();
  slider.min = "0";
  slider.max = String(Math.max(days.length - 1, 0));
  _replayDayIndex = Math.max(days.length - 1, 0);
  slider.value = String(_replayDayIndex);
  document.getElementById("map-replay-metric-c").checked = _replayMetric === "C";
  document.getElementById("map-replay-metric-a").checked = _replayMetric === "A";
  await _refreshReplayMapOverlays();
}

function _paintStaticOrangeHeat(data) {
  const isFc = _isForecastGeoRiskKernel(data);
  const heatPoints = isFc
    ? _normalizeForHeat(data.risk_heatmap)
    : data.risk_heatmap
        .filter((z) => (Number(z.risk_score) || 0) > 0)
        .map((z) => {
          const raw = Number(z.risk_score) || 0;
          return [z.lat, z.lng, raw];
        });
  _applyHeatPoints(heatPoints, isFc);
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

/** @param {any} replayGeo sparse ``eosp_geo_replay_1`` or null */
export async function renderGeoData(data, geoForecast, replayGeo = null) {
  if (!_map) return;

  if (data && data.schema_version) {
    data = _legacyShapeFromBundle(data);
  }

  _lastGeoData = data;
  _lastGeoForecast = geoForecast;

  _ensureLayerGroups();
  _wireLayerToggles();

  _removeHeatLayer();
  for (const ly of [..._mapForecastGroup.getLayers()]) {
    if (ly !== _mapForecastHeatSubgroup && ly !== _mapForecastGreenSubgroup)
      _mapForecastGroup.removeLayer(ly);
  }

  _mapBaseGroup.clearLayers();
  _mapRecordedGroup.clearLayers();

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
      `<strong>${flight.name}</strong><br>` + `${flight.passengers} passengers · Day ${flight.depart_day}`,
    );
    _mapBaseGroup.addLayer(line);
  }

  const replayDays = !!(replayGeo && replayGeo.days && replayGeo.days.length) && _forecastGeoKernel(data);
  const controls = document.getElementById("map-replay-controls");
  if (!replayDays) {
    if (controls) controls.hidden = true;
    _lastReplayGeo = null;
    _paintStaticOrangeHeat(data);
  } else {
    await _setupReplayUi(replayGeo);
  }

  const isForecastGeoPresent = _isForecastGeoRiskKernel(data);
  if (isForecastGeoPresent && _riskSource(data) === ABM_GEO_RISK_SOURCE) {
    const ranked = [...data.risk_heatmap]
      .filter((z) => (Number(z.risk_score) || 0) > 0)
      .sort((a, b) => (Number(b.risk_score) || 0) - (Number(a.risk_score) || 0));
    for (const z of ranked) {
      const raw = Number(z.risk_score) || 0;
      const label = z.city ? `${z.city} (${z.airport_iata})` : z.airport_iata;
      const countryLine = z.country ? `${z.country}<br>` : "";
      const layerCopy =
        `Hub-timeline ensemble: median <strong>cumulative infected stock (metric&nbsp;C)</strong> ` +
        `at last horizon day ≈ <strong>${raw.toFixed(2)}</strong>.<br>` +
        `<em>Scenario Monte Carlo median — not a live flight tracker.</em>`;
      const m = L.circleMarker([z.lat, z.lng], {
        radius: 8,
        fillColor: "transparent",
        color: "transparent",
        interactive: true,
      }).bindPopup(`<strong>${label}</strong><br>` + `${countryLine}` + layerCopy);
      _mapForecastGroup.addLayer(m);
    }
  }

  _renderHubTimelineGreenLayer(data, geoForecast, _greenLayerSelectedByDay(data));

  void _warmReplayCoords();
  _syncMapLayerVisibility();
  _updateMapLegend(data, geoForecast, replayGeo);
}

function _airportLatLng(geoData, iata) {
  const u = String(iata).toUpperCase();
  const z = geoData.risk_heatmap.find((x) => x.airport_iata === u);
  if (z) return { lat: z.lat, lng: z.lng };
  const c = geoData.confirmed_cases.find((x) => x.airport === u);
  if (c) return { lat: c.lat, lng: c.lng };
  return null;
}

function _bucketCumulativeBlock(stats) {
  if (!stats) return null;
  if (typeof stats.median === "number") return stats;
  if (stats.cumulative_infected) return stats.cumulative_infected;
  return null;
}

const _GREEN_SIM_R_MIN = 3;
const _GREEN_SIM_R_SPAN = 9;

/** Green markers: hub stock metric C from ``geo_forecast.by_day`` (full daily series). */
function _renderHubTimelineGreenLayer(data, geoForecast, byDayRow = null) {
  if (!_mapForecastGreenSubgroup) return;
  if (!geoForecast || !geoForecast.by_day || !geoForecast.by_day.length) return;
  _mapForecastGreenSubgroup.clearLayers();

  const dayRow = byDayRow || geoForecast.by_day[geoForecast.by_day.length - 1];
  const entries = Object.entries(dayRow.buckets || {}).filter(([k]) => k !== "ship");
  const positiveMedians = entries
    .map(([, v]) => _bucketCumulativeBlock(v))
    .filter((b) => b != null && Number(b.median) > 0)
    .map((b) => b.median);
  const mx = positiveMedians.length > 0 ? Math.max(...positiveMedians, 1) : 1;
  for (const [code, stats] of entries) {
    const block = _bucketCumulativeBlock(stats);
    if (!block || Number(block.median) <= 0) continue;
    const iata = code.includes("_") ? code.split("_").pop() : code;
    const ll = _airportLatLng(data, iata);
    if (!ll) continue;
    const norm = Math.min(1, Math.max(0, Number(block.median) / mx));
    const t = Math.sqrt(norm);
    const r = Math.max(
      _GREEN_SIM_R_MIN,
      Math.round(_GREEN_SIM_R_MIN + t * _GREEN_SIM_R_SPAN),
    );
    const m = L.circleMarker([ll.lat, ll.lng], {
      radius: r,
      color: "#69f0ae",
      fillColor: "#69f0ae",
      weight: 1,
      fillOpacity: 0.45,
    }).bindPopup(
      `<strong>Simulation · ${iata}</strong><br>` +
        `Median hub stock metric <strong>C</strong> (E+P+I+H+R+D at hub), ` +
        `simulation day <strong>${dayRow.day}</strong> (${String(dayRow.date ?? "").slice(0, 10)}): ` +
        `<strong>${block.median}</strong><br>` +
        `95% CI: ${block.ci_95_lower}–${block.ci_95_upper}<br>` +
        `<em>Green rings follow scrubbed calendar date via full geo buckets (metric C).</em>`,
    );
    _mapForecastGreenSubgroup.addLayer(m);
  }
}

function _updateMapLegend(data, geoForecast, replayGeo) {
  if (!_mapLegendEl) _mapLegendEl = document.getElementById("map-legend");
  if (!_mapLegendEl) return;

  let greenDayRow = geoForecast?.by_day?.length
    ? geoForecast.by_day[geoForecast.by_day.length - 1]
    : null;
  if (replayGeo?.days?.length && _forecastGeoKernel(data) && geoForecast?.by_day?.length) {
    greenDayRow = _byDayRowForCurrentReplayDate();
  }

  const lastDayIdx =
    greenDayRow != null ? greenDayRow.day ?? "—" : geoForecast?.by_day?.length
      ? geoForecast.by_day[geoForecast.by_day.length - 1]?.day ?? "—"
      : "—";
  const greenDate =
    greenDayRow != null && greenDayRow.date != null ? ` (${String(greenDayRow.date).slice(0, 10)})` : "";

  const nTot = geoForecast?.by_day?.length ?? "";

  let heatNote =
    data.metadata?.risk_heatmap_explanation ||
    "Orange-red heat: hub-timeline ensemble median cumulative stock (metric C) at hubs when baseline forecast is cached.";
  let fcHint =
    _riskSource(data) === ABM_GEO_RISK_SOURCE
      ? " Orange matches final-calendar-day C medians unless replay is scrubbed below."
      : "";

  let replayHint = "";
  if (replayGeo?.days?.length && _forecastGeoKernel(data))
    replayHint =
      " Replay slider: scrub calendar days; toggle C vs A — orange heat follows replay sparse medians.";
  let greenHint = geoForecast
    ? ` Green circles: hub stock (C) ensemble — simulation day ${lastDayIdx}${greenDate}` +
      (nTot ? ` (${nTot}-day horizon)` : "") +
      "; radius √-scaled vs busiest hub that day."
    : " Green circles appear once forecast caches geo buckets.";

  const nSim = typeof data.metadata?.n_simulations === "number" ? data.metadata.n_simulations : null;
  const provSim = nSim != null ? ` ${nSim.toLocaleString()} trajectories.` : "";

  _mapLegendEl.innerHTML =
    `<strong>Map layers</strong> — ${heatNote}${fcHint}${replayHint}${provSim} <strong>|</strong> ${greenHint} ` +
    `<strong>|</strong> Recorded vs forecast overlays: use toggles above.`;
}

function _curvedLine(from, to) {
  const points = [];
  const steps = 20;
  for (let i = 0; i <= steps; i++) {
    const t = i / steps;
    const lat = from[0] + (to[0] - from[0]) * t;
    const lng = from[1] + (to[1] - from[1]) * t;
    const arc = Math.sin(Math.PI * t) * Math.abs(to[0] - from[0]) * 0.3;
    points.push([lat + arc, lng]);
  }
  return points;
}
