/**
 * Leaflet world map: ship marker, case markers, evacuation flight arcs,
 * global risk heatmap from GET /api/v1/geo/outbreak.
 */

let _map = null;
let _heatLayer = null;
let _caseMarkers = [];
let _arcLines = [];

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

export function renderGeoData(data) {
  if (!_map) return;

  // Clear previous layers
  _caseMarkers.forEach((m) => m.remove());
  _caseMarkers = [];
  _arcLines.forEach((l) => l.remove());
  _arcLines = [];
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

  // Risk heatmap
  const heatPoints = data.risk_heatmap.map((z) => [z.lat, z.lng, z.risk_score]);
  _heatLayer = L.heatLayer(heatPoints, {
    radius: 30,
    blur: 20,
    maxZoom: 6,
    gradient: { 0.2: "#d89c22", 0.5: "#b95b35", 0.8: "#8b0000", 1.0: "#ff0000" },
  }).addTo(_map);

  // Popup for heatmap airports (transparent overlay circles)
  for (const z of data.risk_heatmap) {
    if (z.ring === 1) continue; // already shown as case marker
    const level = z.risk_score > 0.5 ? "high" : z.risk_score > 0.2 ? "medium" : "low";
    L.circleMarker([z.lat, z.lng], {
      radius: 8, fillColor: "transparent", color: "transparent",
    }).bindPopup(
      `<strong>${z.city} (${z.airport_iata})</strong><br>` +
      `Modelled exposure risk: ${level}<br>` +
      `<em>Ring ${z.ring} — Not a confirmed case location</em>`
    ).addTo(_map);
  }
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
