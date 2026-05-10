/**
 * Boot sequence. Wires all modules together.
 * Executed as ES module after CDN scripts have loaded (they use defer).
 */
import { get } from "./api.js";
import { initMap, renderGeoData } from "./map.js";
import { renderForecast, purgeForecastChart, forecastHorizonDays } from "./chart.js";
import { renderScenarios, SCENARIOS_COMPARE_QUERY, scenarioMetaFromCatalog } from "./scenarios.js";
import { initAuth } from "./auth.js";
import { initDrawer, notifyDrawerOpened } from "./drawer.js";
import { applyCasesList } from "./cases.js";
import { applyTopbarUpdatedFromSummary } from "./topbar-updated.js";

const _DASHBOARD_BOOTSTRAP =
  "/dashboard/bootstrap?" +
  SCENARIOS_COMPARE_QUERY +
  "&version_limit=2&timeline_detail=compact";

let _summaryKpiPollStarted = false;
/** Latest bootstrap payload for optional full-timeline fetch (user-initiated). */
let _timelineBootstrapSnapshot = null;
let _loadFullTimelineWired = false;

async function boot() {
  const authPromise = initAuth();
  const [, data] = await Promise.all([_waitForLibs(), get(_DASHBOARD_BOOTSTRAP)]);
  _applyDashboardBundle(data);

  // World map
  initMap("world-map");
  const meta = data.forecast_baseline?.metadata ?? {};
  const gf = meta.geo_forecast ?? null;
  const rg = meta.replay_geo ?? null;
  if (data.geo) {
    await renderGeoData(data.geo, gf, rg);
  }

  // Researcher drawer wiring (after Clerk — jobs/active needs a token in prod)
  await authPromise;
  await initDrawer(_onRunComplete);
  _wireDrawerToggle();
}

/** @param {any[]|undefined} catalog */
function _hydrateScenarioExplanations(catalog) {
  const root = document.getElementById("scenario-explanations-root");
  const fb = document.getElementById("scenario-explanations-fallback");
  if (!root || !fb) return;
  if (!catalog || !catalog.length) {
    root.innerHTML = "";
    root.hidden = true;
    fb.hidden = false;
    return;
  }
  root.innerHTML = "";
  for (const r of catalog) {
    if (!r?.id) continue;
    const block = document.createElement("div");
    block.className = "scenario-explain-block";
    const h = document.createElement("p");
    h.className = "scenario-explain-title";
    h.textContent = r.public_label || r.id;
    block.appendChild(h);
    if (r.similar_to) {
      const s = document.createElement("p");
      s.className = "scenario-explain-similar";
      s.textContent = r.similar_to;
      block.appendChild(s);
    }
    const t = document.createElement("p");
    t.className = "scenario-explain-tech";
    t.textContent = r.technical_explanation || "";
    block.appendChild(t);
    root.appendChild(block);
  }
  fb.hidden = true;
  root.hidden = false;
}

/** @param {any} data — JSON from ``GET /api/v1/dashboard/bootstrap`` */
function _applyDashboardBundle(data) {
  _timelineBootstrapSnapshot = data;
  _ensureLoadFullTimelineWired();
  _hydrateScenarioExplanations(data.scenario_catalog);
  const catalogMeta = scenarioMetaFromCatalog(data.scenario_catalog);

  if (data.summary) {
    _applySummaryKpis(data.summary);
    if (!_summaryKpiPollStarted) {
      _summaryKpiPollStarted = true;
      setInterval(async () => {
        try {
          const fresh = await get("/cases/summary");
          _applySummaryKpis(fresh);
        } catch (_) {}
      }, 60_000);
    }
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
  const compactPending =
    !!data.forecast_timeline_compact && data.forecast_baseline && !errForecast;
  if (data.forecast_baseline && !errForecast) {
    _applyForecastHorizonUi(data.forecast_baseline);
    if (compactPending) {
      renderForecast(
        "forecast-chart",
        data.forecast_baseline,
        data.forecast_baseline_prev ?? null,
      );
      const expl = document.getElementById("chart-explanation");
      if (expl) {
        expl.textContent +=
          ' Full day-by-day curve and map replay are optional: use "Load full timeline" (extra request).';
      }
      _setForecastTimelineLoading(false);
      _setFullTimelineButtonVisible(true);
    } else {
      renderForecast(
        "forecast-chart",
        data.forecast_baseline,
        data.forecast_baseline_prev ?? null,
      );
      _setForecastTimelineLoading(false);
      _setFullTimelineButtonVisible(false);
    }
    _applyForecastKpis(data.forecast_baseline, data.summary ?? null);
    _setText("chart-freshness", _forecastFreshnessLabel(data.forecast_baseline));
  } else {
    purgeForecastChart("forecast-chart");
    _setForecastTimelineLoading(false);
    _setFullTimelineButtonVisible(false);
    _setText("chart-freshness", "");
    _applyForecastHorizonUi(null);
    _applyForecastKpis(null, data.summary ?? null);
    _setText(
      "chart-explanation",
      errForecast ? `Forecast not loaded: ${errForecast}` : "Forecast not loaded.",
    );
  }

  const errScenarios = data.errors?.scenarios;
  if (data.scenarios && !errScenarios) {
    renderScenarios(data.scenarios, { catalogMeta });
  } else {
    renderScenarios(null, {
      fetchError: errScenarios || "Scenario compare unavailable.",
      catalogMeta,
    });
  }

  applyCasesList(Array.isArray(data.cases) ? data.cases : []);
}

function _onRunComplete() {
  get(_DASHBOARD_BOOTSTRAP, { cache: "no-store" })
    .then(async (data) => {
      _applyDashboardBundle(data);
      if (data.geo) {
        const m = data.forecast_baseline?.metadata ?? {};
        const gf = m.geo_forecast ?? null;
        const rg = m.replay_geo ?? null;
        await renderGeoData(data.geo, gf, rg);
      }
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

function _setForecastTimelineLoading(busy) {
  const el = document.getElementById("forecast-timeline-loading");
  if (!el) return;
  el.hidden = !busy;
  el.setAttribute("aria-busy", busy ? "true" : "false");
}

function _setFullTimelineButtonVisible(show) {
  const btn = document.getElementById("btn-load-full-timeline");
  if (!btn) return;
  btn.hidden = !show;
}

function _ensureLoadFullTimelineWired() {
  if (_loadFullTimelineWired) return;
  const btn = document.getElementById("btn-load-full-timeline");
  if (!btn) return;
  _loadFullTimelineWired = true;
  btn.addEventListener("click", () => {
    const d = _timelineBootstrapSnapshot;
    if (!d?.forecast_timeline_compact || d.errors?.forecast_baseline) return;
    void _hydrateFullForecastTimeline(d);
  });
}

/**
 * User-initiated fetch: full baseline (and previous-version baseline) for chart + map replay.
 * Default bootstrap stays compact so casual visitors do not trigger extra DB reads.
 *
 * @param {any} data bootstrap JSON
 */
async function _hydrateFullForecastTimeline(data) {
  if (!data.forecast_timeline_compact || data.errors?.forecast_baseline) {
    _setForecastTimelineLoading(false);
    return;
  }
  const btn = document.getElementById("btn-load-full-timeline");
  if (btn) btn.disabled = true;
  _setForecastTimelineLoading(true);
  try {
    const fetches = [get("/forecasts/baseline")];
    const vPrev = data.versions?.versions?.[1]?.version_id;
    if (vPrev) {
      fetches.push(
        get("/forecasts/baseline?model_version=" + encodeURIComponent(vPrev)),
      );
    }
    const results = await Promise.all(fetches);
    const full = results[0];
    const fullPrev = results[1] ?? null;
    const errForecast = data.errors?.forecast_baseline;
    if (full && !errForecast) {
      _applyForecastHorizonUi(full);
      renderForecast("forecast-chart", full, fullPrev);
      _applyForecastKpis(full, data.summary ?? null);
      _setText("chart-freshness", _forecastFreshnessLabel(full));
      const meta = full.metadata ?? {};
      const gf = meta.geo_forecast ?? null;
      const rg = meta.replay_geo ?? null;
      if (data.geo) {
        await renderGeoData(data.geo, gf, rg);
      }
      _setFullTimelineButtonVisible(false);
    }
  } catch (_) {
    const fb = data.forecast_baseline;
    const fp = data.forecast_baseline_prev ?? null;
    if (fb) {
      renderForecast("forecast-chart", fb, fp);
      _setText("chart-explanation", "Full timeline unavailable; showing last day from bundle.");
    }
  } finally {
    _setForecastTimelineLoading(false);
    if (btn) btn.disabled = false;
  }
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

/** @param {any} forecastResponse */
function _applyForecastHorizonUi(forecastResponse) {
  const h = forecastHorizonDays(forecastResponse);
  const chartH = document.getElementById("chart-heading-horizon");
  if (chartH) {
    chartH.textContent =
      h != null
        ? `Baseline Forecast · Cumulative cases · ${h}-day horizon`
        : "Baseline Forecast · Cumulative cases · simulation horizon";
  }
  const casesLbl = document.getElementById("kpi-label-forecast-cases");
  const deathsLbl = document.getElementById("kpi-label-forecast-deaths");
  const casesTxt = h != null ? `${h}-day forecast · Cases` : "Forecast · Cases";
  const deathsTxt = h != null ? `${h}-day forecast · Deaths` : "Forecast · Deaths";
  if (casesLbl) casesLbl.textContent = casesTxt;
  if (deathsLbl) deathsLbl.textContent = deathsTxt;
  const tipC = document.getElementById("kpi-forecast-cases-tip");
  const tipD = document.getElementById("kpi-forecast-deaths-tip");
  const tipCases =
    h != null
      ? `Values are at day ${h} of the hub-timeline simulation (median cumulative infections and uncertainty).`
      : "Forecasted cumulative infections at end of simulation horizon (median and uncertainty).";
  const tipDeaths =
    h != null
      ? `Values are at day ${h} of the hub-timeline simulation (median cumulative deaths and uncertainty).`
      : "Forecasted cumulative deaths at end of simulation horizon from the baseline scenario (median and uncertainty).";
  if (tipC) tipC.setAttribute("data-tip", tipCases);
  if (tipD) tipD.setAttribute("data-tip", tipDeaths);
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
  applyTopbarUpdatedFromSummary(s);
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

boot().catch((err) => {
  const p = document.createElement("p");
  p.style.cssText =
    "background:#5a1e1e;color:#e57373;padding:12px 24px;font-family:monospace";
  p.textContent = "Dashboard failed to load: " + (err && err.message ? err.message : String(err));
  document.body.prepend(p);
});
