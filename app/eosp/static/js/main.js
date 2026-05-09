/**
 * Boot sequence. Wires all modules together.
 * Executed as ES module after CDN scripts have loaded (they use defer).
 */
import { get } from "./api.js";
import { initMap, renderGeoData } from "./map.js";
import { renderForecast, purgeForecastChart } from "./chart.js";
import { renderScenarios, SCENARIOS_COMPARE_QUERY } from "./scenarios.js";
import { initAuth } from "./auth.js";
import { initDrawer, notifyDrawerOpened } from "./drawer.js";
import { applyCasesList } from "./cases.js";

async function boot() {
  await initAuth();
  // Wait for CDN libraries (Leaflet and Plotly use defer, so they may not
  // be ready immediately when the module executes on fast connections)
  await _waitForLibs();

  // Parallel data fetch
  const [summary, forecast, versions, scenarios, geo, caseLines] = await Promise.allSettled([
    get("/cases/summary"),
    get("/forecasts/baseline"),
    get("/inference/versions?limit=2"),
    get("/scenarios/compare?" + SCENARIOS_COMPARE_QUERY),
    get("/geo/outbreak"),
    get("/cases"),
  ]);

  if (summary.status === "fulfilled") {
    _applySummaryKpis(summary.value);
    setInterval(async () => {
      try {
        const fresh = await get("/cases/summary");
        _applySummaryKpis(fresh);
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

  if (forecast.status === "fulfilled") {
    const prev = versions.status === "fulfilled" && versions.value.versions.length > 1
      ? await get(`/forecasts/baseline?model_version=${versions.value.versions[1].version_id}`).catch(() => null)
      : null;
    renderForecast("forecast-chart", forecast.value, prev);
    _applyForecastKpis(
      forecast.value,
      summary.status === "fulfilled" ? summary.value : null,
    );
    _setText("chart-freshness", _forecastFreshnessLabel(forecast.value));
  } else {
    purgeForecastChart("forecast-chart");
    const msg = forecast.reason instanceof Error ? forecast.reason.message : String(forecast.reason);
    _setText("chart-freshness", "");
    _applyForecastKpis(
      null,
      summary.status === "fulfilled" ? summary.value : null,
    );
    _setText("chart-explanation", msg ? `Forecast not loaded: ${msg}` : "Forecast not loaded.");
  }

  // Scenario cards (always render shell; partial API responses / failures still show cards)
  if (scenarios.status === "fulfilled") {
    renderScenarios(scenarios.value);
  } else {
    const msg = scenarios.reason instanceof Error ? scenarios.reason.message : String(scenarios.reason);
    renderScenarios(null, { fetchError: msg });
  }

  if (caseLines.status === "fulfilled") {
    const rows = caseLines.value;
    applyCasesList(Array.isArray(rows) ? rows : []);
  } else {
    const msg = caseLines.reason instanceof Error ? caseLines.reason.message : String(caseLines.reason);
    applyCasesList(null, msg);
  }

  // World map
  initMap("world-map");
  if (geo.status === "fulfilled") {
    const gf = forecast.status === "fulfilled" ? forecast.value.metadata?.geo_forecast : null;
    renderGeoData(geo.value, gf);
  }

  // Researcher drawer wiring
  await initDrawer(_onRunComplete);
  _wireDrawerToggle();
}

function _onRunComplete() {
  Promise.allSettled([
    get("/cases/summary"),
    get("/forecasts/baseline"),
    get("/scenarios/compare?" + SCENARIOS_COMPARE_QUERY),
    get("/inference/versions?limit=2"),
    get("/geo/outbreak"),
    get("/cases"),
  ]).then(([summary, forecast, scenarios, versions, geo, caseLines]) => {
    if (summary.status === "fulfilled") {
      _applySummaryKpis(summary.value);
    }
    if (forecast.status === "fulfilled") {
      renderForecast("forecast-chart", forecast.value);
      _applyForecastKpis(forecast.value, summary.status === "fulfilled" ? summary.value : null);
      _setText("chart-freshness", _forecastFreshnessLabel(forecast.value));
    } else {
      purgeForecastChart("forecast-chart");
      const msg = forecast.reason instanceof Error ? forecast.reason.message : String(forecast.reason);
      _setText("chart-freshness", "");
      _applyForecastKpis(null, summary.status === "fulfilled" ? summary.value : null);
      _setText("chart-explanation", msg ? `Forecast not loaded: ${msg}` : "Forecast not loaded.");
    }
    if (scenarios.status === "fulfilled") {
      renderScenarios(scenarios.value);
    } else {
      const msg = scenarios.reason instanceof Error ? scenarios.reason.message : String(scenarios.reason);
      renderScenarios(null, { fetchError: msg });
    }
    if (geo.status === "fulfilled") {
      const gf = forecast.status === "fulfilled" ? forecast.value.metadata?.geo_forecast : null;
      renderGeoData(geo.value, gf);
    }
    if (versions.status === "fulfilled" && versions.value.versions.length) {
      const v = versions.value.versions[0];
      const badge = document.getElementById("version-badge");
      if (badge) badge.textContent = v.version_id;
    }
    if (caseLines.status === "fulfilled") {
      const rows = caseLines.value;
      applyCasesList(Array.isArray(rows) ? rows : []);
    } else {
      const msg = caseLines.reason instanceof Error ? caseLines.reason.message : String(caseLines.reason);
      applyCasesList(null, msg);
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
    notifyDrawerOpened();
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

function _setTopbarStatusLines(summary) {
  const updatedAt = new Date(summary.last_updated);
  _setText("topbar-status-updated", `Updated ${_relativeTime(updatedAt)}`);
  const checkedRaw = summary.external_feed_last_checked_at;
  const sub = checkedRaw != null && String(checkedRaw).trim() !== ""
    ? `(Last checked for new cases ${_relativeTime(new Date(checkedRaw))})`
    : `(Last checked for new cases —)`;
  _setText("topbar-status-checked", sub);
}

function _setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

/** @param {any} s */
function _applySummaryKpis(s) {
  const totalRecorded = s.total_confirmed + s.total_suspected;
  _setText("kpi-confirmed", totalRecorded);
  _setText("kpi-confirmed-sub", `${Object.keys(s.data_sources).length} data sources`);
  _setRecordedCasesTooltip(s);
  _setText("kpi-deaths", s.total_deaths);
  const cfr = totalRecorded > 0
    ? Math.round((s.total_deaths / totalRecorded) * 100) + "%"
    : "—";
  _setText("kpi-deaths-sub", `CFR ${cfr}`);
  _setTopbarStatusLines(s);
}

/** @param {any} s */
function _setRecordedCasesTooltip(s) {
  const tip = document.getElementById("kpi-recorded-tip");
  if (!tip) return;
  const breakdown = `${s.total_confirmed} confirmed · ${s.total_suspected} suspected`;
  const rest =
    "Person-equivalent totals: individual rows plus ingested cohort or summary observations (each cohort row counts as cohort_size confirmed/suspected persons).";
  tip.setAttribute("data-tip", `${breakdown}. ${rest}`);
  tip.setAttribute("aria-label", `Recorded cases: ${breakdown}`);
}

/** @param {any} forecastResponse baseline JSON or null @param {any} summaryFallback optional /cases/summary for death fan if forecast body missing */
function _applyForecastKpis(forecastResponse, summaryFallback) {
  const last = forecastResponse?.forecast?.at(-1);
  if (!last) {
    _setText("kpi-forecast", "—");
    _setText("kpi-forecast-sub", "");
    const sm = summaryFallback;
    if (
      sm != null
      && sm.forecast_deaths_median_14d != null
      && sm.forecast_deaths_ci_95_lower_14d != null
      && sm.forecast_deaths_ci_95_upper_14d != null
    ) {
      _setText("kpi-forecast-deaths", sm.forecast_deaths_median_14d);
      _setText(
        "kpi-forecast-deaths-sub",
        `95% CI: ${sm.forecast_deaths_ci_95_lower_14d}–${sm.forecast_deaths_ci_95_upper_14d} deaths`,
      );
    } else {
      _setText("kpi-forecast-deaths", "—");
      _setText("kpi-forecast-deaths-sub", "");
    }
    return;
  }
  const cases = last.cases_cumulative;
  _setText("kpi-forecast", cases.median);
  _setText("kpi-forecast-sub", `95% CI: ${cases.ci_95_lower}–${cases.ci_95_upper} cases`);
  const deaths = last.deaths_cumulative;
  _setText("kpi-forecast-deaths", deaths.median);
  _setText("kpi-forecast-deaths-sub", `95% CI: ${deaths.ci_95_lower}–${deaths.ci_95_upper} deaths`);
}

function _forecastFreshnessLabel(forecast) {
  const n = forecast?.metadata?.n_simulations;
  if (typeof n === "number") {
    return `${n.toLocaleString()} simulations`;
  }
  return "Simulation result loaded";
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
