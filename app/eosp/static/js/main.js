/**
 * Boot sequence. Wires all modules together.
 * Executed as ES module after CDN scripts have loaded (they use defer).
 */
import { get } from "./api.js";
import { initMap, renderGeoData } from "./map.js";
import { renderForecast } from "./chart.js";
import { renderScenarios } from "./scenarios.js";
import { initDrawer } from "./drawer.js";

async function boot() {
  // Wait for CDN libraries (Leaflet and Plotly use defer, so they may not
  // be ready immediately when the module executes on fast connections)
  await _waitForLibs();

  // Parallel data fetch
  const [summary, forecast, versions, scenarios, geo] = await Promise.allSettled([
    get("/cases/summary"),
    get("/forecasts/baseline"),
    get("/inference/versions?limit=2"),
    get("/scenarios/compare?scenarios=baseline,quarantine_immediate,evacuation_delay_7d,enhanced_destination_protocols"),
    get("/geo/outbreak"),
  ]);

  // KPI cards
  if (summary.status === "fulfilled") {
    const s = summary.value;
    _setText("kpi-confirmed", s.total_confirmed);
    _setText("kpi-confirmed-sub", `+0 today · ${Object.keys(s.data_sources).length} sources`);
    _setText("kpi-suspected", s.total_suspected);
    _setText("kpi-suspected-sub", "PCR pending");
    _setText("kpi-deaths", s.total_deaths);
    const cfr = s.total_confirmed > 0
      ? Math.round((s.total_deaths / (s.total_confirmed)) * 100) + "%"
      : "—";
    _setText("kpi-deaths-sub", `CFR ${cfr} · Expected: 35–50%`);
    const updatedAt = new Date(s.last_updated);
    _setText("topbar-status", `Updated ${_relativeTime(updatedAt)}`);
    setInterval(async () => {
      try {
        const fresh = await get("/cases/summary");
        _setText("topbar-status", `Updated ${_relativeTime(new Date(fresh.last_updated))}`);
      } catch (_) {}
    }, 60_000);
  }

  // Version badge
  if (versions.status === "fulfilled" && versions.value.versions.length) {
    const v = versions.value.versions[0];
    const badge = document.getElementById("version-badge");
    if (badge) {
      badge.textContent = v.version_id;
      badge.className = "version-badge " + (v.convergence_status === "CONVERGED" ? "converged" : v.convergence_status === "WARNING" ? "warning" : "failed");
    }
  }

  // Forecast chart
  if (forecast.status === "fulfilled") {
    const prev = versions.status === "fulfilled" && versions.value.versions.length > 1
      ? await get(`/forecasts/baseline?model_version=${versions.value.versions[1].version_id}`).catch(() => null)
      : null;
    renderForecast("forecast-chart", forecast.value, prev);
    const last = forecast.value.forecast.at(-1);
    if (last) {
      _setText("kpi-forecast", last.cases_cumulative.median);
      _setText("kpi-forecast-sub", `95% CI: ${last.cases_cumulative.ci_95_lower}–${last.cases_cumulative.ci_95_upper} cases`);
    }
    _setText("chart-freshness", forecast.value.metadata?.freshness_status === "provisional"
      ? "Provisional (100 sims)" : "Full fidelity (10k sims)");
  }

  // Scenario cards
  if (scenarios.status === "fulfilled") {
    renderScenarios(scenarios.value);
  }

  // World map
  initMap("world-map");
  if (geo.status === "fulfilled") {
    renderGeoData(geo.value);
  }

  // Researcher drawer wiring
  initDrawer(_onRunComplete);
  _wireDrawerToggle();
}

function _onRunComplete() {
  // Reload forecast and scenarios after a run finishes
  Promise.allSettled([
    get("/forecasts/baseline"),
    get("/scenarios/compare?scenarios=baseline,quarantine_immediate,evacuation_delay_7d,enhanced_destination_protocols"),
    get("/inference/versions?limit=2"),
    get("/geo/outbreak"),
  ]).then(([forecast, scenarios, versions, geo]) => {
    if (forecast.status === "fulfilled") renderForecast("forecast-chart", forecast.value);
    if (scenarios.status === "fulfilled") renderScenarios(scenarios.value);
    if (geo.status === "fulfilled") renderGeoData(geo.value);
    if (versions.status === "fulfilled" && versions.value.versions.length) {
      const v = versions.value.versions[0];
      const badge = document.getElementById("version-badge");
      if (badge) badge.textContent = v.version_id;
    }
  });
}

function _wireDrawerToggle() {
  const overlay = document.getElementById("drawer-overlay");
  const pageBody = document.getElementById("page-body");
  const btnOpen  = document.getElementById("btn-open-drawer");
  const btnClose = document.getElementById("btn-close-drawer");

  btnOpen?.addEventListener("click", () => {
    overlay?.classList.add("open");
    pageBody?.classList.add("dimmed");
    document.getElementById("drawer")?.focus();
  });

  function close() {
    overlay?.classList.remove("open");
    pageBody?.classList.remove("dimmed");
  }

  btnClose?.addEventListener("click", close);
  overlay?.addEventListener("click", (e) => {
    if (e.target === overlay) close();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && overlay?.classList.contains("open")) close();
  });
}

function _waitForLibs(attempts = 0) {
  return new Promise((resolve) => {
    if (typeof L !== "undefined" && typeof Plotly !== "undefined") return resolve();
    if (attempts > 40) return resolve(); // give up after 2 seconds, render what we can
    setTimeout(() => _waitForLibs(attempts + 1).then(resolve), 50);
  });
}

function _setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function _relativeTime(date) {
  const diff = Math.round((Date.now() - date.getTime()) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.round(diff / 60)}m ago`;
  return `${Math.round(diff / 3600)}h ago`;
}

boot().catch((err) => {
  const p = document.createElement("p");
  p.style.cssText =
    "background:#5a1e1e;color:#e57373;padding:12px 24px;font-family:monospace";
  p.textContent = "Dashboard failed to load: " + (err && err.message ? err.message : String(err));
  document.body.prepend(p);
});
