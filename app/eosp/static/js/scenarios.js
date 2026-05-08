/**
 * Renders scenario comparison cards in the sidebar.
 * Clicking a card fetches that scenario's full forecast and overlays it
 * on the chart.
 */
import { get } from "./api.js";
import { overlayScenario, clearScenarioOverlay } from "./chart.js";

const SCENARIO_META = {
  baseline: { label: "No Intervention · Baseline", desc: "Full contact network, inferred parameters.", color: "rgb(11,124,131)" },
  quarantine_immediate: { label: "Immediate Quarantine", desc: "All passengers confined to cabins from day 1.", color: "rgb(47,125,79)" },
  evacuation_delay_3d: { label: "Evacuation Delayed +3 Days", desc: "Ship stays in place 3 extra days before flights depart.", color: "rgb(185,91,53)" },
  evacuation_delay_7d: { label: "Evacuation Delayed +7 Days", desc: "Ship stays in place 7 extra days before flights depart.", color: "rgb(185,91,53)" },
  enhanced_destination_protocols: { label: "Enhanced Arrival Protocols", desc: "All evacuees tested and isolated on arrival; contacts monitored.", color: "rgb(47,125,79)" },
};

/** Same order as the dashboard compare request — single source of truth. */
export const DEFAULT_COMPARE_SCENARIOS = [
  "baseline",
  "quarantine_immediate",
  "evacuation_delay_7d",
  "enhanced_destination_protocols",
];

export const SCENARIOS_COMPARE_QUERY =
  "scenarios=" + DEFAULT_COMPARE_SCENARIOS.join(",");

let _activeScenario = null;

/**
 * @param {object|null} comparison - API payload, or null when the compare request failed
 * @param {{ fetchError?: string }} [opts]
 */
export function renderScenarios(comparison, opts = {}) {
  const container = document.getElementById("scenario-sidebar");
  if (!container) return;
  container.innerHTML = "";

  if (opts.fetchError) {
    const banner = document.createElement("p");
    banner.className = "scenario-sidebar-banner";
    banner.textContent = opts.fetchError;
    container.appendChild(banner);
  }

  const byName = comparison
    ? Object.fromEntries(comparison.scenarios.map((s) => [s.name, s]))
    : {};
  const unavailable = new Set(comparison?.scenarios_unavailable || []);

  for (const name of DEFAULT_COMPARE_SCENARIOS) {
    const s = byName[name];
    const locked = !s || unavailable.has(name) || opts.fetchError;
    if (s && !unavailable.has(name) && !opts.fetchError) {
      _appendInteractiveCard(container, s);
    } else {
      _appendLockedCard(container, name, locked && !opts.fetchError ? "No simulation result yet. Run Analysis for this scenario." : null);
    }
  }

  const customCard = document.createElement("div");
  customCard.className = "scenario-card locked";
  customCard.title = "Define custom intervention parameters. Requires researcher access — click Run Analysis.";
  customCard.innerHTML = `
    <div class="scenario-name" style="color:#2e4a47">+ Custom Scenario</div>
    <div class="scenario-desc" style="color:#2e4a47">Click ⚗ Run Analysis to define custom parameters.</div>
  `;
  container.appendChild(customCard);
}

function _appendInteractiveCard(container, s) {
  const meta = SCENARIO_META[s.name] || { label: s.name.replace(/_/g, " "), desc: "", color: "rgb(11,124,131)" };
  const isBaseline = s.name === "baseline";
  const delta = s.vs_baseline;

  let deltaHtml = `<span class="scenario-delta ref">Reference</span>`;
  let cardClass = "baseline";
  if (delta) {
    const pct = delta.case_change_pct;
    const sign = pct < 0 ? "↓" : "↑";
    const cls = pct < 0 ? "down" : "up";
    cardClass = pct < 0 ? "better" : "worse";
    deltaHtml = `<span class="scenario-delta ${cls}">${sign} ${Math.abs(pct)}% vs baseline</span>`;
  }

  const valueColor = isBaseline ? "#d89c22" : (delta?.case_change_pct < 0 ? "#2f7d4f" : "#b95b35");

  const card = document.createElement("div");
  card.className = `scenario-card ${cardClass}`;
  card.dataset.scenario = s.name;
  card.setAttribute("role", "button");
  card.setAttribute("tabindex", "0");
  card.innerHTML = `
      <div class="scenario-name">${meta.label}</div>
      <div class="scenario-value" style="color:${valueColor}">${s.cases_by_day_14.median}</div>
      ${deltaHtml}
      <div class="scenario-ci">95% CI: ${s.cases_by_day_14.ci_95[0]}–${s.cases_by_day_14.ci_95[1]} cases</div>
      <div class="scenario-desc">${meta.desc}</div>
    `;

  card.addEventListener("click", () => _onScenarioClick(s.name, meta.color, card));
  card.addEventListener("keydown", (e) => { if (e.key === "Enter") card.click(); });
  container.appendChild(card);
}

function _appendLockedCard(container, name, subtitle) {
  const meta = SCENARIO_META[name] || { label: name.replace(/_/g, " "), desc: "", color: "rgb(11,124,131)" };
  const card = document.createElement("div");
  card.className = "scenario-card locked";
  card.dataset.scenario = name;
  const hint = subtitle || "Forecast not loaded yet.";
  card.innerHTML = `
      <div class="scenario-name">${meta.label}</div>
      <div class="scenario-value scenario-value-muted">—</div>
      <div class="scenario-ci">${hint}</div>
      <div class="scenario-desc">${meta.desc}</div>
    `;
  container.appendChild(card);
}

async function _onScenarioClick(scenarioName, color, card) {
  if (scenarioName === "baseline") {
    clearScenarioOverlay("forecast-chart");
    document.querySelectorAll(".scenario-card").forEach((c) => c.classList.remove("active"));
    _activeScenario = null;
    return;
  }
  if (_activeScenario === scenarioName) {
    clearScenarioOverlay("forecast-chart");
    card.classList.remove("active");
    _activeScenario = null;
    return;
  }
  clearScenarioOverlay("forecast-chart");
  document.querySelectorAll(".scenario-card").forEach((c) => c.classList.remove("active"));
  try {
    const forecast = await get(`/forecasts/${scenarioName}`);
    overlayScenario("forecast-chart", forecast, color);
    card.classList.add("active");
    _activeScenario = scenarioName;
  } catch (err) {
    console.warn("Could not load scenario forecast:", err.message);
  }
}
