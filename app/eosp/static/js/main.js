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

const _DASHBOARD_BOOTSTRAP =
  "/dashboard/bootstrap?" + SCENARIOS_COMPARE_QUERY + "&version_limit=2";

async function boot() {
  const authPromise = initAuth();
  const [, data] = await Promise.all([_waitForLibs(), get(_DASHBOARD_BOOTSTRAP)]);
  _applyDashboardBundle(data);

  // World map
  initMap("world-map");
  const gf = data.forecast_baseline?.metadata?.geo_forecast ?? null;
  if (data.geo) {
    renderGeoData(data.geo, gf);
  }

  // Researcher drawer wiring (after Clerk — jobs/active needs a token in prod)
  await authPromise;
  await initDrawer(_onRunComplete);
  _wireDrawerToggle();
}

/** @param {any} data — JSON from ``GET /api/v1/dashboard/bootstrap`` */
function _applyDashboardBundle(data) {
  if (data.summary) {
    _applySummaryKpis(data.summary);
    setInterval(async () => {
      try {
        const fresh = await get("/cases/summary");
        _applySummaryKpis(fresh);
      } catch (_) {}
    }, 60_000);
  }

  const versions = data.versions;
  if (versions?.versions?.length) {
    const v = versions.versions[0];
    const badge = document.getElementById("version-badge");
    if (badge) {
      badge.textContent = v.version_id;
      badge.className = "version-badge " + (v.convergence_status === "CONVERGED" ? "converged" : v.convergence_status === "WARNING" ? "warning" : "failed");
    }
  }

  const errForecast = data.errors?.forecast_baseline;
  if (data.forecast_baseline && !errForecast) {
    renderForecast(
      "forecast-chart",
      data.forecast_baseline,
      data.forecast_baseline_prev ?? null,
    );
    _applyForecastKpis(data.forecast_baseline, data.summary ?? null);
    _setText("chart-freshness", _forecastFreshnessLabel(data.forecast_baseline));
  } else {
    purgeForecastChart("forecast-chart");
    _setText("chart-freshness", "");
    _applyForecastKpis(null, data.summary ?? null);
    _setText(
      "chart-explanation",
      errForecast ? `Forecast not loaded: ${errForecast}` : "Forecast not loaded.",
    );
  }

  const errScenarios = data.errors?.scenarios;
  if (data.scenarios && !errScenarios) {
    renderScenarios(data.scenarios);
  } else {
    renderScenarios(null, { fetchError: errScenarios || "Scenario compare unavailable." });
  }

  applyCasesList(Array.isArray(data.cases) ? data.cases : []);
}

function _onRunComplete() {
  get(_DASHBOARD_BOOTSTRAP)
    .then((data) => {
      if (data.summary) {
        _applySummaryKpis(data.summary);
      }
      const errForecast = data.errors?.forecast_baseline;
      if (data.forecast_baseline && !errForecast) {
        renderForecast("forecast-chart", data.forecast_baseline);
        _applyForecastKpis(data.forecast_baseline, data.summary ?? null);
        _setText("chart-freshness", _forecastFreshnessLabel(data.forecast_baseline));
      } else {
        purgeForecastChart("forecast-chart");
        _setText("chart-freshness", "");
        _applyForecastKpis(null, data.summary ?? null);
        _setText(
          "chart-explanation",
          errForecast ? `Forecast not loaded: ${errForecast}` : "Forecast not loaded.",
        );
      }
      const errScenarios = data.errors?.scenarios;
      if (data.scenarios && !errScenarios) {
        renderScenarios(data.scenarios);
      } else {
        renderScenarios(null, { fetchError: errScenarios || "Scenario compare unavailable." });
      }
      if (data.geo) {
        const gf = data.forecast_baseline?.metadata?.geo_forecast ?? null;
        renderGeoData(data.geo, gf);
      }
      const versions = data.versions;
      if (versions?.versions?.length) {
        const v = versions.versions[0];
        const badge = document.getElementById("version-badge");
        if (badge) badge.textContent = v.version_id;
      }
      applyCasesList(Array.isArray(data.cases) ? data.cases : []);
    })
    .catch(() => {});
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
