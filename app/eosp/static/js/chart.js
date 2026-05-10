/**
 * Plotly forecast timeseries with 95% and 50% CI bands.
 * renderForecast(forecastResponse, prevForecastResponse?)
 * overlayScenario(scenarioResponse, color) / clearScenarioOverlay()
 */

const LAYOUT = {
  paper_bgcolor: "#112820",
  plot_bgcolor: "#112820",
  font: { family: "Inter, sans-serif", color: "#e0f0ed", size: 11 },
  margin: { t: 10, r: 10, b: 40, l: 38 },
  xaxis: {
    gridcolor: "#1e3530", tickcolor: "#1e3530", linecolor: "#1e3530",
    tickfont: { size: 10 },
  },
  yaxis: {
    gridcolor: "#1e3530", tickcolor: "#1e3530", linecolor: "#1e3530",
    tickfont: { size: 10 }, title: { text: "Cumulative cases", font: { size: 10 } },
    rangemode: "tozero",
  },
  legend: { bgcolor: "transparent", font: { size: 10 } },
  hovermode: "x unified",
  hoverlabel: { bgcolor: "#071410", bordercolor: "#1e3530", font: { size: 11 } },
  showlegend: true,
};

const CONFIG = { displayModeBar: false, responsive: true };

/**
 * Simulation horizon length in days (not the number of points in a compact bundle).
 *
 * @param {any} forecast — API ``ForecastResponse`` shape
 * @returns {number|null}
 */
export function forecastHorizonDays(forecast) {
  const m = forecast?.metadata;
  if (!m || typeof m !== "object") {
    const n = forecast?.forecast?.length;
    return typeof n === "number" && n > 0 ? n : null;
  }
  if (typeof m.horizon_days === "number") return m.horizon_days;
  const sht = m.scenario_hub_timeline;
  if (sht && typeof sht.horizon_days === "number") return sht.horizon_days;
  const gfm = m.geo_forecast?.metadata;
  if (gfm && typeof gfm.n_days === "number") return gfm.n_days;
  const n = forecast?.forecast?.length;
  return typeof n === "number" && n > 0 ? n : null;
}

/** Plotly filled bands need at least two x values; duplicate a single-day forecast. */
function _padSinglePointSeries(dates, ...series) {
  if (dates.length !== 1) return { dates, series };
  const d0 = dates[0];
  return {
    dates: [d0, d0],
    series: series.map((arr) => [arr[0], arr[0]]),
  };
}

export function renderForecast(containerId, forecast, prevForecast) {
  let dates = forecast.forecast.map((p) => p.date);
  let med    = forecast.forecast.map((p) => p.cases_cumulative.median);
  let ci95lo = forecast.forecast.map((p) => p.cases_cumulative.ci_95_lower);
  let ci95hi = forecast.forecast.map((p) => p.cases_cumulative.ci_95_upper);
  let ci50lo = forecast.forecast.map((p) => p.cases_cumulative.ci_50_lower ?? p.cases_cumulative.median);
  let ci50hi = forecast.forecast.map((p) => p.cases_cumulative.ci_50_upper ?? p.cases_cumulative.median);

  const padded = _padSinglePointSeries(dates, med, ci95lo, ci95hi, ci50lo, ci50hi);
  dates = padded.dates;
  [med, ci95lo, ci95hi, ci50lo, ci50hi] = padded.series;

  const traces = [
    // 95% CI lower (invisible base for fill)
    { x: dates, y: ci95lo, type: "scatter", mode: "lines", line: { width: 0 },
      showlegend: false, hoverinfo: "skip", name: "ci95lo" },
    // 95% CI band
    { x: dates, y: ci95hi, type: "scatter", mode: "lines", fill: "tonexty",
      fillcolor: "rgba(11,124,131,0.12)", line: { width: 0 },
      name: "95% CI", hovertemplate: "95% CI: %{y}<extra></extra>" },
    // 50% CI lower
    { x: dates, y: ci50lo, type: "scatter", mode: "lines", line: { width: 0 },
      showlegend: false, hoverinfo: "skip", name: "ci50lo" },
    // 50% CI band
    { x: dates, y: ci50hi, type: "scatter", mode: "lines", fill: "tonexty",
      fillcolor: "rgba(11,124,131,0.25)", line: { width: 0 },
      name: "50% CI", hovertemplate: "50% CI: %{y}<extra></extra>" },
    // Median line
    { x: dates, y: med, type: "scatter", mode: "lines",
      line: { color: "#0b7c83", width: 2 },
      name: "Median forecast", hovertemplate: "Median: %{y}<extra></extra>" },
  ];

  // Previous version median (dashed grey)
  if (prevForecast) {
    let prevMed = prevForecast.forecast.map((p) => p.cases_cumulative.median);
    let prevDates = prevForecast.forecast.map((p) => p.date);
    const pp = _padSinglePointSeries(prevDates, prevMed);
    prevDates = pp.dates;
    prevMed = pp.series[0];
    traces.push({
      x: prevDates, y: prevMed, type: "scatter", mode: "lines",
      line: { color: "#4a7a72", width: 1, dash: "dash" },
      name: "Previous version", hovertemplate: "Prev: %{y}<extra></extra>",
    });
  }

  Plotly.react(containerId, traces, LAYOUT, CONFIG);

  const expl = document.getElementById("chart-explanation");
  if (expl) {
    const last = forecast.forecast.at(-1);
    if (last) {
      const m = Math.round(last.cases_cumulative.median);
      const lo = Math.round(last.cases_cumulative.ci_95_lower);
      const hi = Math.round(last.cases_cumulative.ci_95_upper);
      const metaH = forecastHorizonDays(forecast) ?? forecast.forecast.length;
      const lastDayIdx =
        typeof last.day === "number"
          ? last.day
          : forecastHorizonDays(forecast) ?? forecast.forecast.length;
      expl.textContent = `Day ${lastDayIdx} of ${metaH} · median ${m} cumulative cases · 95% CI ${lo}–${hi}.`;
    }
  }
}

/** Remove traces so a failed reload does not leave a stale chart. */
export function purgeForecastChart(containerId) {
  const el = document.getElementById(containerId);
  if (el && typeof Plotly !== "undefined") {
    try {
      Plotly.purge(el);
    } catch (_) {
      /* ignore */
    }
  }
}

export function overlayScenario(containerId, forecast, color) {
  const dates = forecast.forecast.map((p) => p.date);
  const med    = forecast.forecast.map((p) => p.cases_cumulative.median);
  const ci95lo = forecast.forecast.map((p) => p.cases_cumulative.ci_95_lower);
  const ci95hi = forecast.forecast.map((p) => p.cases_cumulative.ci_95_upper);

  Plotly.addTraces(containerId, [
    { x: dates, y: ci95lo, type: "scatter", mode: "lines", line: { width: 0 },
      showlegend: false, hoverinfo: "skip", name: "scen_ci_lo" },
    { x: dates, y: ci95hi, type: "scatter", mode: "lines", fill: "tonexty",
      fillcolor: color.replace(")", ",0.15)").replace("rgb", "rgba"),
      line: { width: 0 }, name: "Scenario 95% CI", hoverinfo: "skip" },
    { x: dates, y: med, type: "scatter", mode: "lines",
      line: { color, width: 2, dash: "dot" },
      name: "Scenario median", hovertemplate: "Scenario: %{y}<extra></extra>" },
  ]);
}

export function clearScenarioOverlay(containerId) {
  const gd = document.getElementById(containerId);
  if (!gd || !gd.data) return;
  const toRemove = gd.data
    .map((t, i) => ({ name: t.name, i }))
    .filter((t) => ["scen_ci_lo", "Scenario 95% CI", "Scenario median"].includes(t.name))
    .map((t) => t.i)
    .reverse();
  if (toRemove.length) Plotly.deleteTraces(containerId, toRemove);
}
