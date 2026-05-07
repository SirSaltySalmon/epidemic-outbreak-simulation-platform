async function getJson(path) {
  const response = await fetch(path);
  if (!response.ok) {
    throw new Error(`${path} returned ${response.status}`);
  }
  return response.json();
}

async function getJsonOrNull(path) {
  const response = await fetch(path);
  if (response.status === 503 || response.status === 404) {
    return null;
  }
  if (!response.ok) {
    throw new Error(`${path} returned ${response.status}`);
  }
  return response.json();
}

function renderForecastEmptyState() {
  const chart = document.querySelector("#forecastChart");
  if (chart) {
    chart.innerHTML =
      '<p class="empty-state">No forecast has been run yet. Click <strong>Run Forecasts</strong> to generate one — visitors will then see this result instantly.</p>';
  }
  const estimate = document.querySelector("#estimate");
  if (estimate) {
    estimate.textContent = "—";
  }
}

function renderScenarioEmptyState() {
  const list = document.querySelector("#scenarioList");
  if (list) {
    list.innerHTML =
      '<p class="empty-state">Scenario comparisons appear here after the first forecast run.</p>';
  }
}

function rangeText(metric) {
  return `${metric.median} [${metric.ci_95_lower}-${metric.ci_95_upper}]`;
}

function renderForecast(points) {
  const chart = document.querySelector("#forecastChart");
  const max = Math.max(...points.map((point) => point.cases_cumulative.ci_95_upper));
  chart.innerHTML = points
    .map((point) => {
      const width = Math.max(4, (point.cases_cumulative.median / max) * 100);
      return `
        <div class="bar-row">
          <span>${point.date.slice(5)}</span>
          <div class="bar-track"><div class="bar" style="width:${width}%"></div></div>
          <strong>${rangeText(point.cases_cumulative)}</strong>
        </div>
      `;
    })
    .join("");
}

function renderScenarios(comparison) {
  const list = document.querySelector("#scenarioList");
  list.innerHTML = comparison.scenarios
    .map((scenario) => {
      const delta = scenario.vs_baseline ? `${scenario.vs_baseline.case_change_pct}% vs baseline` : "Reference";
      return `
        <div class="scenario">
          <strong>${scenario.name.replaceAll("_", " ")}</strong>
          <span>${scenario.cases_by_day_14.median} cases by day 14 (${delta})</span>
        </div>
      `;
    })
    .join("");
}

function renderForecastRuns(runs) {
  const list = document.querySelector("#forecastRunList");
  if (!list) {
    return;
  }
  list.innerHTML = runs
    .map((run) => {
      return `<div>${run.scenario.replaceAll("_", " ")}: ${run.cases_day_14.median} cases by day 14</div>`;
    })
    .join("");
}

function renderCases(cases) {
  const rows = document.querySelector("#caseRows");
  rows.innerHTML = cases
    .map((caseRecord) => {
      return `
        <tr>
          <td>${caseRecord.case_id.slice(0, 8)}</td>
          <td>${caseRecord.symptom_onset_date}</td>
          <td>${caseRecord.location_country}</td>
          <td>${caseRecord.confirmed_or_suspected}</td>
          <td>${caseRecord.lab_test_result}</td>
          <td>${caseRecord.validation_score.toFixed(2)}</td>
        </tr>
      `;
    })
    .join("");
}

async function boot() {
  const [summary, forecast, versions, scenarios, cases] = await Promise.all([
    getJson("/api/v1/cases/summary"),
    getJsonOrNull("/api/v1/forecasts/baseline"),
    getJson("/api/v1/inference/versions?limit=1"),
    getJsonOrNull("/api/v1/scenarios/compare?scenarios=baseline,quarantine_immediate,evacuation_delay_7d"),
    getJson("/api/v1/cases"),
  ]);

  document.querySelector("#confirmedCases").textContent = summary.total_confirmed;
  document.querySelector("#suspectedCases").textContent = summary.total_suspected;
  document.querySelector("#deaths").textContent = summary.total_deaths;
  document.querySelector("#updatedAt").textContent = `Data updated ${new Date(summary.last_updated).toLocaleString()}`;
  document.querySelector("#versionBadge").textContent = versions.versions[0].version_id;

  if (forecast && Array.isArray(forecast.forecast) && forecast.forecast.length > 0) {
    const finalPoint = forecast.forecast.at(-1);
    document.querySelector("#estimate").textContent = rangeText(finalPoint.cases_cumulative);
    renderForecast(forecast.forecast);
  } else {
    renderForecastEmptyState();
  }

  if (scenarios && Array.isArray(scenarios.scenarios) && scenarios.scenarios.length > 0) {
    renderScenarios(scenarios);
  } else {
    renderScenarioEmptyState();
  }

  renderCases(cases);
  bindCaseForm();
  bindForecastRunner();
  const runs = await getJson("/api/v1/forecast-runs?limit=3");
  renderForecastRuns(runs.runs);
}

function bindForecastRunner() {
  const button = document.querySelector("#runForecastsButton");
  if (!button || button.dataset.bound === "true") {
    return;
  }
  button.dataset.bound = "true";
  button.addEventListener("click", async () => {
    button.disabled = true;
    button.textContent = "Running...";
    try {
      const response = await fetch("/api/v1/forecasts/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          scenarios: ["baseline", "quarantine_immediate", "evacuation_delay_7d"],
          model_version: "latest",
          n_simulations: 10000,
        }),
      });
      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || `Request failed with ${response.status}`);
      }
      const result = await response.json();
      renderForecastRuns(result.forecasts);
      const [forecast, scenarios] = await Promise.all([
        getJsonOrNull("/api/v1/forecasts/baseline"),
        getJsonOrNull("/api/v1/scenarios/compare?scenarios=baseline,quarantine_immediate,evacuation_delay_7d"),
      ]);
      if (forecast && Array.isArray(forecast.forecast) && forecast.forecast.length > 0) {
        const finalPoint = forecast.forecast.at(-1);
        document.querySelector("#estimate").textContent = rangeText(finalPoint.cases_cumulative);
        renderForecast(forecast.forecast);
      }
      if (scenarios && Array.isArray(scenarios.scenarios) && scenarios.scenarios.length > 0) {
        renderScenarios(scenarios);
      }
    } finally {
      button.disabled = false;
      button.textContent = "Run Forecasts";
    }
  });
}

function bindCaseForm() {
  const form = document.querySelector("#caseForm");
  const status = document.querySelector("#intakeStatus");
  if (!form || form.dataset.bound === "true") {
    return;
  }
  form.dataset.bound = "true";
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = form.querySelector("button");
    button.disabled = true;
    status.textContent = "Submitting...";
    const formData = new FormData(form);
    const payload = Object.fromEntries(formData.entries());
    for (const field of ["hospitalization_date", "location_airport_code"]) {
      if (!payload[field]) {
        payload[field] = null;
      }
    }
    payload.contacts = [];
    payload.updated_by = "dashboard";
    payload.updated_reason = "New_case";
    try {
      const response = await fetch("/api/v1/cases", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || `Request failed with ${response.status}`);
      }
      const result = await response.json();
      status.textContent = `Saved, score ${result.validation.quality_score.toFixed(2)}`;
      const [summary, cases] = await Promise.all([
        getJson("/api/v1/cases/summary"),
        getJson("/api/v1/cases"),
      ]);
      document.querySelector("#confirmedCases").textContent = summary.total_confirmed;
      document.querySelector("#suspectedCases").textContent = summary.total_suspected;
      document.querySelector("#deaths").textContent = summary.total_deaths;
      document.querySelector("#updatedAt").textContent = `Data updated ${new Date(summary.last_updated).toLocaleString()}`;
      renderCases(cases);
      form.patient_identifier.value = `anon_local_${Date.now()}`;
    } catch (error) {
      status.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });
}

boot().catch((error) => {
  document.body.insertAdjacentHTML("afterbegin", `<p class="panel">Dashboard failed to load: ${error.message}</p>`);
});
