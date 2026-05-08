/**
 * Renders scenario comparison cards in the sidebar.
 * Clicking a card fetches that scenario's full forecast and overlays it
 * on the chart.
 */
import { get } from "./api.js";
import { overlayScenario, clearScenarioOverlay } from "./chart.js";

const SCENARIO_META = {
  baseline:                      { label: "No Intervention · Baseline",         desc: "Full contact network, inferred parameters.",                          color: "rgb(11,124,131)" },
  quarantine_immediate:          { label: "Immediate Quarantine",                desc: "All passengers confined to cabins from day 1.",                       color: "rgb(47,125,79)" },
  evacuation_delay_3d:           { label: "Evacuation Delayed +3 Days",          desc: "Ship stays in place 3 extra days before flights depart.",             color: "rgb(185,91,53)" },
  evacuation_delay_7d:           { label: "Evacuation Delayed +7 Days",          desc: "Ship stays in place 7 extra days before flights depart.",             color: "rgb(185,91,53)" },
  enhanced_destination_protocols: { label: "Enhanced Arrival Protocols",         desc: "All evacuees tested and isolated on arrival; contacts monitored.",    color: "rgb(47,125,79)" },
};

let _activeScenario = null;

export function renderScenarios(comparison) {
  const container = document.getElementById("scenario-sidebar");
  if (!container) return;
  container.innerHTML = "";

  for (const s of comparison.scenarios) {
    const meta = SCENARIO_META[s.name] || { label: s.name.replace(/_/g, " "), desc: "", color: "rgb(11,124,131)" };
    const isBaseline = s.name === "baseline";
    const delta = s.vs_baseline;

    let deltaHtml = `<span class="scenario-delta ref">Reference</span>`;
    let cardClass = "baseline";
    if (delta) {
      const pct = delta.case_change_pct;
      const sign = pct < 0 ? "↓" : "↑";
      const cls  = pct < 0 ? "down" : "up";
      cardClass  = pct < 0 ? "better" : "worse";
      deltaHtml  = `<span class="scenario-delta ${cls}">${sign} ${Math.abs(pct)}% vs baseline</span>`;
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

  // Custom scenario placeholder
  const customCard = document.createElement("div");
  customCard.className = "scenario-card locked";
  customCard.title = "Define custom intervention parameters. Requires researcher access — click Run Analysis.";
  customCard.innerHTML = `
    <div class="scenario-name" style="color:#2e4a47">+ Custom Scenario</div>
    <div class="scenario-desc" style="color:#2e4a47">Click ⚗ Run Analysis to define custom parameters.</div>
  `;
  container.appendChild(customCard);
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
