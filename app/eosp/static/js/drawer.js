/**
 * Researcher drawer: idle and running states.
 * Manages stage cards, SSE subscription, mini Plotly charts.
 */
import { post, get } from "./api.js";
import { subscribe } from "./jobs.js";

const SCENARIOS = [
  { id: "baseline",                      label: "Baseline" },
  { id: "quarantine_immediate",          label: "Quarantine Now" },
  { id: "evacuation_delay_3d",           label: "Delay +3d" },
  { id: "evacuation_delay_7d",           label: "Delay +7d" },
  { id: "enhanced_destination_protocols", label: "Enhanced Protocols" },
];
const FIDELITY = [
  { value: 100,   label: "100 (fast preview)" },
  { value: 1000,  label: "1,000" },
  { value: 10000, label: "10,000 (full)" },
];

let _selectedScenarios = new Set(["baseline"]);
let _selectedFidelity = 100;
let _cancelSSE = null;
let _lastRun = null;
let _inferenceEtaTick = null;

function _clearInferenceEtaTicker() {
  if (_inferenceEtaTick) {
    clearInterval(_inferenceEtaTick);
    _inferenceEtaTick = null;
  }
}

function _startInferenceEtaTicker() {
  _clearInferenceEtaTicker();
  const t0 = Date.now();
  _inferenceEtaTick = setInterval(() => {
    const el = document.querySelector('.stage-card[data-stage="2"] .inference-eta');
    if (!el) {
      _clearInferenceEtaTicker();
      return;
    }
    const s = Math.floor((Date.now() - t0) / 1000);
    el.textContent = `Elapsed: ${s}s · completion ETA not streamed (wait for sampler)`;
  }, 1000);
}

function _formatEtaSeconds(sec) {
  if (!Number.isFinite(sec) || sec < 0) return "—";
  if (sec < 90) return `~${Math.max(1, Math.round(sec))}s`;
  if (sec < 7200) return `~${Math.round(sec / 60)}m`;
  return `~${Math.round(sec / 3600)}h`;
}

/** Remaining time for Monte Carlo from server elapsed_s + progress. */
function _simEtaLine(data) {
  const done = data.trajectories ?? 0;
  const total = data.total ?? 0;
  const elapsed = data.elapsed_s;
  if (done > 0 && total > 0 && done < total && typeof elapsed === "number" && elapsed > 0.05) {
    const rate = done / elapsed;
    const remain = (total - done) / rate;
    return `Est. remaining: ${_formatEtaSeconds(remain)}`;
  }
  return "";
}

export function initDrawer(onRunComplete) {
  _renderIdle(onRunComplete);
}

function _renderIdle(onRunComplete) {
  _clearInferenceEtaTicker();
  const body = document.getElementById("drawer-body");
  body.innerHTML = "";

  // Scenario selector
  body.appendChild(_section("Scenarios", _pillGroup(
    SCENARIOS, (id) => _selectedScenarios.has(id),
    (id, selected) => { if (selected) _selectedScenarios.add(id); else _selectedScenarios.delete(id); },
    true
  )));

  // Fidelity selector
  body.appendChild(_section("Simulation fidelity", _pillGroup(
    FIDELITY.map((f) => ({ id: String(f.value), label: f.label })),
    (id) => _selectedFidelity === Number(id),
    (id) => { _selectedFidelity = Number(id); },
    false
  )));

  // Run button
  const btnRun = document.createElement("button");
  btnRun.className = "btn-run";
  btnRun.textContent = "Run Forecast →";
  btnRun.addEventListener("click", () => _startRun(onRunComplete));
  body.appendChild(btnRun);

  // Last run summary
  if (_lastRun) {
    const section = _section("Last completed run", _lastRunTable(_lastRun));
    body.appendChild(section);
  }

  // Download links
  const dlSection = _section("Downloads", _downloadLinks());
  body.appendChild(dlSection);
}

async function _startRun(onRunComplete) {
  const scenarios = [..._selectedScenarios];
  if (!scenarios.length) return;

  let jobId;
  try {
    const result = await post("/inference/run", {
      reason: "manual",
      scenarios: [..._selectedScenarios],
      n_simulations: _selectedFidelity,
    });
    jobId = result.job_id;
  } catch (err) {
    alert("Failed to start run: " + err.message);
    return;
  }

  _renderRunning();
  _updateStage(1, "active", { n_cases: "…", quality_mean: "…" });

  _cancelSSE = subscribe(
    jobId,
    (event) => _handleEvent(event),
    (event) => _handleComplete(event, jobId, onRunComplete),
  );
}

function _handleEvent(event) {
  if (event.stage === "cases") {
    _updateStage(1, "done", event);
    _startInferenceEtaTicker();
    _updateStage(2, "active", {});
  } else if (event.stage === "inference") {
    _clearInferenceEtaTicker();
    _updateStage(2, "active", event);
  } else if (event.stage === "simulation") {
    _updateStage(2, "done", {});
    _updateStage(3, "active", event);
  }
}

async function _handleComplete(event, jobId, onRunComplete) {
  _cancelSSE = null;
  _clearInferenceEtaTicker();
  _updateStage(3, "done", {});
  _updateStage(4, "active", { job_id: jobId, detail: event.detail });

  // Fetch the job record for the version
  try {
    const record = await get(`/inference/jobs/${jobId}`);
    _lastRun = record;
    _updateStage(4, "done", record);
  } catch (_) {}

  if (typeof onRunComplete === "function") onRunComplete();
}

function _renderRunning() {
  _clearInferenceEtaTicker();
  const body = document.getElementById("drawer-body");
  body.innerHTML = "";

  const stages = [
    { n: 1, title: "① Cases & Network",        state: "pending" },
    { n: 2, title: "② Bayesian Inference",      state: "pending" },
    { n: 3, title: "③ Monte Carlo Simulation",  state: "pending" },
    { n: 4, title: "④ Results",                 state: "pending" },
  ];

  for (const s of stages) {
    const card = _stageCard(s.n, s.title, s.state);
    body.appendChild(card);
  }
}

function _updateStage(n, state, data) {
  const card = document.querySelector(`.stage-card[data-stage="${n}"]`);
  if (!card) return;

  card.className = `stage-card ${state}`;
  const badge = card.querySelector(".stage-badge");
  if (badge) {
    badge.className = `stage-badge badge-${state === "done" ? "done" : state === "active" ? "running" : "waiting"}`;
    badge.textContent = state === "done" ? "✓ DONE" : state === "active" ? "● RUNNING" : "WAITING";
  }

  const body = card.querySelector(".stage-body");
  if (!body) return;
  body.innerHTML = _stageBodyHtml(n, state, data);

  // Render mini Plotly charts if needed
  if (n === 2 && state === "active" && data.params) {
    _renderPosteriorChart(data);
  }
  if (n === 3 && state === "active" && data.fan_sample) {
    _renderFanChart(data);
  }
  if (n === 4 && state === "active") {
    const btn = card.querySelector(".btn-view-dashboard");
    if (btn) btn.addEventListener("click", () => document.getElementById("btn-close-drawer").click());
  }
}

function _stageBodyHtml(n, state, data) {
  if (state === "done") return `<div style="color:var(--ink-muted);font-size:0.78rem">${_doneSummary(n, data)}</div>`;
  if (state === "pending") return "";

  if (n === 1) {
    return `<div style="color:var(--teal-light);font-size:0.82rem">
      Cases: <strong>${data.n_cases ?? "…"}</strong> ·
      Mean quality: <strong>${typeof data.quality_mean === "number" ? data.quality_mean.toFixed(2) : "…"}</strong>
    </div>`;
  }
  if (n === 2) {
    if (state === "active" && data.status === "skipped") {
      return `<div style="color:var(--ink-muted);font-size:0.82rem">Inference skipped (${
        (data.reason || "").replace(/</g, "")
      }) — using stored posterior.</div>`;
    }
    if (state === "active" && data.status !== "complete" && data.status !== "skipped") {
      return `
        <div style="color:var(--teal-light);font-size:0.82rem">Running NUTS sampler…</div>
        <div class="progress-eta inference-eta" style="color:var(--ink-muted);font-size:0.78rem">Starting…</div>
      `;
    }
    const draw = data.draw ?? 0;
    const total = data.total ?? 2000;
    const pct = Math.round((draw / total) * 100);
    const rhat = typeof data.rhat_max === "number" ? data.rhat_max.toFixed(3) : "…";
    const rhatClass = data.rhat_max < 1.01 ? "good" : data.rhat_max < 1.05 ? "warn" : "fail";
    const div = data.divergences ?? 0;
    const chains = (data.chain_rhat || []).map((r) =>
      `<div class="chain-dot ${r < 1.01 ? "good" : r < 1.05 ? "warn" : "fail"}" title="R̂ ${r.toFixed(3)}"></div>`
    ).join("");
    const chips = Object.entries(data.params || {}).slice(0, 5).map(([k, v]) =>
      `<div class="param-chip">${k} <span class="pval">${typeof v === "number" ? v.toFixed(3) : v}</span></div>`
    ).join("");
    return `
      <div class="stat-row">
        <div class="stat-box"><div class="stat-lbl">Draws</div><div class="stat-val">${draw.toLocaleString()}</div><div class="stat-sub">/ ${total.toLocaleString()}</div></div>
        <div class="stat-box"><div class="stat-lbl">R̂ max</div><div class="stat-val">${rhat}</div><div class="stat-sub ${rhatClass}">${rhatClass === "good" ? "✓ &lt;1.01" : rhatClass === "warn" ? "⚠ &lt;1.05" : "✗ high"}</div></div>
        <div class="stat-box"><div class="stat-lbl">Divergences</div><div class="stat-val">${div}</div><div class="stat-sub ${div < 50 ? "good" : "fail"}">${div < 50 ? "✓ &lt;50" : "✗ high"}</div></div>
      </div>
      <div class="progress-bar"><div class="progress-fill" style="width:${pct}%"></div></div>
      <div class="progress-eta">Chain status: <span class="chain-row">${chains}</span></div>
      <div class="param-chips">${chips}</div>
      <div id="posterior-chart-${Date.now()}"></div>
    `;
  }
  if (n === 3) {
    const done = data.trajectories ?? 0;
    const total = data.total ?? 10000;
    const pct = Math.round((done / total) * 100);
    return `
      <div class="stat-row">
        <div class="stat-box"><div class="stat-lbl">Trajectories</div><div class="stat-val">${done.toLocaleString()}</div><div class="stat-sub">/ ${total.toLocaleString()}</div></div>
        <div class="stat-box"><div class="stat-lbl">Scenario</div><div class="stat-val" style="font-size:0.9rem">${(data.scenario || "").replace(/_/g, " ")}</div></div>
        <div class="stat-box"><div class="stat-lbl">Progress</div><div class="stat-val">${pct}%</div></div>
      </div>
      <div class="progress-bar"><div class="progress-fill" style="width:${pct}%"></div></div>
      <div class="progress-eta" style="color:var(--ink-muted);font-size:0.78rem">${
        _simEtaLine(data) || "Estimating time…"
      }</div>
      <div id="fan-chart-${Date.now()}"></div>
    `;
  }
  if (n === 4) {
    return `
      <div class="result-banner">✓ Forecast complete</div>
      <button class="btn-view-dashboard">← View updated dashboard</button>
    `;
  }
  return "";
}

function _doneSummary(n, data) {
  if (n === 1) return `${data.n_cases ?? "?"} cases · quality ${typeof data.quality_mean === "number" ? data.quality_mean.toFixed(2) : "?"}`;
  if (n === 2) return `Inference complete · R̂ ${data.rhat_max != null ? Number(data.rhat_max).toFixed(3) : "?"} · ${data.divergences ?? "?"} divergences`;
  if (n === 3) return `Simulation complete · ${(data.total || data.trajectories || "10,000").toLocaleString()} trajectories`;
  if (n === 4) return "Forecast saved and dashboard updated.";
  return "Done";
}

function _renderPosteriorChart(data) {
  const el = document.querySelector("[id^='posterior-chart-']");
  if (!el || !window.Plotly) return;
  const values = Object.values(data.params || {}).slice(0, 1);
  if (!values.length) return;
  Plotly.newPlot(el.id, [{
    x: [values[0]], type: "histogram",
    marker: { color: "rgba(11,124,131,0.6)" },
    nbinsx: 20,
  }], {
    paper_bgcolor: "#071410", plot_bgcolor: "#071410",
    font: { color: "#e0f0ed", size: 9 },
    margin: { t: 4, r: 4, b: 20, l: 24 },
    height: 80,
    xaxis: { gridcolor: "#1e3530", tickfont: { size: 8 } },
    yaxis: { gridcolor: "#1e3530", tickfont: { size: 8 } },
    showlegend: false,
  }, { displayModeBar: false, responsive: true });
}

function _renderFanChart(data) {
  const el = document.querySelector("[id^='fan-chart-']");
  if (!el || !window.Plotly || !data.fan_sample?.length) return;
  const traces = data.fan_sample.map((traj, i) => ({
    y: traj, type: "scatter", mode: "lines",
    line: { color: "rgba(11,124,131,0.3)", width: 1 },
    showlegend: false, hoverinfo: "skip",
  }));
  Plotly.newPlot(el.id, traces, {
    paper_bgcolor: "#071410", plot_bgcolor: "#071410",
    font: { color: "#e0f0ed", size: 9 },
    margin: { t: 4, r: 4, b: 20, l: 24 },
    height: 80,
    xaxis: { gridcolor: "#1e3530", tickfont: { size: 8 } },
    yaxis: { gridcolor: "#1e3530", tickfont: { size: 8 } },
    showlegend: false,
  }, { displayModeBar: false, responsive: true });
}

function _stageCard(n, title, state) {
  const card = document.createElement("div");
  card.className = `stage-card ${state}`;
  card.dataset.stage = n;
  const badgeClass = state === "done" ? "badge-done" : state === "active" ? "badge-running" : "badge-waiting";
  const badgeText  = state === "done" ? "✓ DONE" : state === "active" ? "● RUNNING" : "WAITING";
  card.innerHTML = `
    <div class="stage-header">
      <span class="stage-title">${title}</span>
      <span class="stage-badge ${badgeClass}">${badgeText}</span>
    </div>
    <div class="stage-body"></div>
  `;
  card.querySelector(".stage-header").addEventListener("click", () => {
    if (state !== "done") return;
    card.classList.toggle("expanded");
  });
  return card;
}

function _section(label, content) {
  const div = document.createElement("div");
  div.className = "config-section";
  const lbl = document.createElement("div");
  lbl.className = "config-label";
  lbl.textContent = label;
  div.appendChild(lbl);
  div.appendChild(content);
  return div;
}

function _pillGroup(items, isSelected, onToggle, multiSelect) {
  const group = document.createElement("div");
  group.className = "pill-group";
  for (const item of items) {
    const pill = document.createElement("button");
    pill.className = "pill" + (isSelected(item.id) ? " selected" : "");
    pill.type = "button";
    pill.textContent = item.label;
    pill.addEventListener("click", () => {
      if (multiSelect) {
        const nowSelected = !pill.classList.contains("selected");
        pill.classList.toggle("selected", nowSelected);
        onToggle(item.id, nowSelected);
      } else {
        group.querySelectorAll(".pill").forEach((p) => p.classList.remove("selected"));
        pill.classList.add("selected");
        onToggle(item.id);
      }
    });
    group.appendChild(pill);
  }
  return group;
}

function _lastRunTable(run) {
  const table = document.createElement("table");
  table.className = "last-run-table";
  const rows = [
    ["Version",    run.detail?.inference_version ?? "—"],
    ["Trigger",    run.reason ?? "—"],
    ["Status",     run.status ?? "—"],
    ["Completed",  run.completed_at ? new Date(run.completed_at).toLocaleTimeString() : "—"],
  ];
  table.innerHTML = rows.map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join("");
  return table;
}

function _downloadLinks() {
  const div = document.createElement("div");
  div.className = "download-links";
  const links = [
    { href: "/api/v1/forecasts/baseline",                      label: "↓ Forecast JSON (baseline)" },
    { href: "/api/v1/validation/hindcast-accuracy",            label: "↓ Hindcast validation report" },
  ];
  for (const l of links) {
    const a = document.createElement("a");
    a.className = "download-link";
    a.href = l.href;
    a.target = "_blank";
    a.rel = "noopener";
    a.textContent = l.label;
    div.appendChild(a);
  }
  return div;
}
