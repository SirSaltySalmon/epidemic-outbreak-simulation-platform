/**
 * Researcher drawer: idle and running states.
 * Manages stage cards, SSE subscription, mini Plotly charts.
 */
import { post, get, patch, del } from "./api.js";
import { getSessionToken } from "./auth.js";
import { subscribe } from "./jobs.js";
import { markResearchConsoleCaseDataUpdated } from "./topbar-updated.js";

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
let _onRunCompleteCb = null;
let _stageData = new Map();
let _refCountries = [];
let _refAirportsAll = [];
/** @type {Promise<void> | null} */
let _refGeoLoadPromise = null;
/** @type {string | null} */
let _editingCaseId = null;
/** @type {Record<string, unknown> | null} */
let _prefillCase = null;

async function _loadReferenceGeo() {
  try {
    const [cRes, aRes] = await Promise.all([
      get("/reference/countries"),
      get("/reference/airports"),
    ]);
    _refCountries = cRes.countries || [];
    _refAirportsAll = aRes.airports || [];
  } catch (_) {
    _refCountries = [];
    _refAirportsAll = [];
  }
}

function _ensureReferenceGeoLoaded() {
  if (!_refGeoLoadPromise) {
    _refGeoLoadPromise = _loadReferenceGeo();
  }
  return _refGeoLoadPromise;
}

function _refreshCaseIntakeGeoSelects() {
  const form = document.querySelector("#drawer-body form.case-intake-form");
  if (!form) return;
  const cSel = form.querySelector('[name="location_country"]');
  if (!cSel || cSel.tagName !== "SELECT") return;
  const prev = cSel.value;
  cSel.innerHTML = _countrySelectOptions();
  if (prev && [...cSel.options].some((o) => o.value === prev)) {
    cSel.value = prev;
  }
  const apt = form.querySelector('[name="location_airport_code"]')?.value || "";
  _syncAirportSelect(form, apt);
}

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

export async function initDrawer(onRunComplete) {
  _onRunCompleteCb = onRunComplete;
  await _renderIdle(onRunComplete);
}

/** Call when the researcher drawer opens: resume SSE if a pipeline job is already running. */
export async function notifyDrawerOpened() {
  await _ensureReferenceGeoLoaded();
  _refreshCaseIntakeGeoSelects();
  await _tryAttachActiveJob(_onRunCompleteCb);
}

function _setRunButtonDisabled(disabled) {
  const btn = document.querySelector("#drawer-body .btn-run");
  if (!btn) return;
  btn.disabled = !!disabled;
  btn.classList.toggle("disabled", !!disabled);
}

async function _tryAttachActiveJob(onRunComplete) {
  try {
    const res = await get("/inference/jobs/active");
    const job = res.job;
    if (job && (job.status === "running" || job.status === "pending")) {
      _clearInferenceEtaTicker();
      _renderRunning();
      _cancelSSE = subscribe(
        job.job_id,
        getSessionToken,
        (event) => _handleEvent(event),
        (event) => _handleComplete(event, job.job_id, onRunComplete),
      );
    }
  } catch (_) {
    /* ignore */
  }
}

async function _renderIdle(onRunComplete) {
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
  btnRun.type = "button";
  btnRun.className = "btn-run";
  btnRun.textContent = "Run Forecast →";
  btnRun.addEventListener("click", () => _startRun(onRunComplete));
  body.appendChild(btnRun);
  _syncRunButtonWithActivePipeline();

  // Last run summary
  if (_lastRun) {
    const section = _section("Last completed run", _lastRunTable(_lastRun));
    body.appendChild(section);
  }

  body.appendChild(_caseIntakeSection(onRunComplete));
  body.appendChild(await _manageCasesSection(onRunComplete));

  // Download links
  const dlSection = _section("Downloads", _downloadLinks());
  body.appendChild(dlSection);
}

function _optionalDateStr(value) {
  const s = value != null && String(value).trim();
  return s ? s : null;
}

function _escapeHtml(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function _countrySelectOptions() {
  const head = '<option value="">— Select country —</option>';
  if (!_refCountries.length) return head;
  return (
    head +
    _refCountries.map((c) => `<option value="${c.code}">${c.code} — ${c.name}</option>`).join("")
  );
}

function _syncAirportSelect(form, preserveIata) {
  const country = String(form.querySelector('[name="location_country"]')?.value || "").trim();
  const aptSel = form.querySelector('[name="location_airport_code"]');
  if (!aptSel || aptSel.tagName !== "SELECT") return;
  const list = country
    ? _refAirportsAll.filter((a) => a.country === country)
    : _refAirportsAll;
  aptSel.innerHTML =
    '<option value="">— Optional airport —</option>' +
    list.map((a) => `<option value="${a.iata}">${a.label}</option>`).join("");
  const keep = preserveIata && list.some((a) => a.iata === preserveIata) ? preserveIata : "";
  if (keep) aptSel.value = keep;
}

function _applyCohortFieldVisibility(form) {
  const kind = form.querySelector('[name="observation_kind"]')?.value || "individual";
  const cohortBlock = form.querySelector(".cohort-only-fields");
  const deathRow = form.querySelector(".death-date-field");
  if (cohortBlock) cohortBlock.hidden = kind !== "cohort";
  if (deathRow) deathRow.hidden = kind === "cohort";
}

function _applyPrefillToForm(form) {
  if (!_prefillCase) return;
  const p = _prefillCase;
  form.querySelector('[name="patient_identifier"]').value = p.patient_identifier ?? "";
  form.querySelector('[name="symptom_onset_date"]').value = String(p.symptom_onset_date || "").slice(0, 10);
  form.querySelector('[name="hospitalization_date"]').value = p.hospitalization_date
    ? String(p.hospitalization_date).slice(0, 10)
    : "";
  form.querySelector('[name="death_date"]').value = p.death_date ? String(p.death_date).slice(0, 10) : "";
  form.querySelector('[name="location_country"]').value = (p.location_country || "").toUpperCase();
  _syncAirportSelect(form, p.location_airport_code || "");
  form.querySelector('[name="confirmed_or_suspected"]').value = p.confirmed_or_suspected || "suspected";
  form.querySelector('[name="lab_test_result"]').value = p.lab_test_result || "not_tested";
  form.querySelector('[name="data_source"]').value = p.data_source || "Manual_Form";
  const obs = p.observation_kind || "individual";
  form.querySelector('[name="observation_kind"]').value = obs;
  if (obs === "cohort") {
    form.querySelector('[name="cohort_size"]').value = String(p.cohort_size ?? "");
    form.querySelector('[name="cohort_deaths"]').value = String(p.cohort_deaths ?? "");
    form.querySelector('[name="report_period_start"]').value = p.report_period_start
      ? String(p.report_period_start).slice(0, 10)
      : "";
    form.querySelector('[name="report_period_end"]').value = p.report_period_end
      ? String(p.report_period_end).slice(0, 10)
      : "";
  }
  _applyCohortFieldVisibility(form);
  const sub = form.querySelector(".btn-case-submit");
  const can = form.querySelector(".btn-case-cancel");
  if (sub) sub.textContent = _editingCaseId ? "Save changes" : "Add case to database";
  if (can) can.hidden = !_editingCaseId;
}

function _caseIntakeSection(onRunComplete) {
  const form = document.createElement("form");
  form.className = "case-intake-form";
  form.innerHTML = `
    <div class="case-field-grid">
      <label class="case-field case-field-span">Observation
        <select name="observation_kind">
          <option value="individual">Individual</option>
          <option value="cohort">Cohort / aggregate</option>
        </select>
      </label>
      <label class="case-field">Patient ID
        <input name="patient_identifier" type="text" required maxlength="120" autocomplete="off" />
      </label>
      <label class="case-field">Symptom onset
        <input name="symptom_onset_date" type="date" required />
      </label>
      <label class="case-field">Country
        <select name="location_country" required>${_countrySelectOptions()}</select>
      </label>
      <label class="case-field">Airport
        <select name="location_airport_code"></select>
      </label>
      <label class="case-field">Status
        <select name="confirmed_or_suspected">
          <option value="confirmed">Confirmed</option>
          <option value="suspected">Suspected</option>
        </select>
      </label>
      <label class="case-field">Lab result
        <select name="lab_test_result">
          <option value="not_tested">Not tested</option>
          <option value="PCR_positive">PCR positive</option>
          <option value="PCR_negative">PCR negative</option>
          <option value="serology_positive">Serology positive</option>
        </select>
      </label>
      <label class="case-field">Hospitalization
        <input name="hospitalization_date" type="date" />
      </label>
      <label class="case-field death-date-field">Death (individual)
        <input name="death_date" type="date" />
      </label>
      <div class="cohort-only-fields case-field-span cohort-only-grid" hidden>
        <label class="case-field">Cohort size
          <input name="cohort_size" type="number" min="1" step="1" value="1" />
        </label>
        <label class="case-field">Cohort deaths
          <input name="cohort_deaths" type="number" min="0" step="1" value="0" />
        </label>
        <label class="case-field">Report period start
          <input name="report_period_start" type="date" />
        </label>
        <label class="case-field">Report period end
          <input name="report_period_end" type="date" />
        </label>
      </div>
      <label class="case-field case-field-span">Data source
        <input name="data_source" type="text" value="Manual_Form" required />
      </label>
    </div>
    <div class="case-form-actions">
      <button type="submit" class="btn-case-submit">Add case to database</button>
      <button type="button" class="btn-case-cancel" hidden>Cancel edit</button>
    </div>
    <p class="case-intake-hint" id="case-intake-msg" aria-live="polite"></p>
  `;
  form.querySelector('[name="location_country"]').addEventListener("change", () => {
    _syncAirportSelect(form, "");
  });
  form.querySelector('[name="observation_kind"]').addEventListener("change", () => {
    _applyCohortFieldVisibility(form);
  });
  _syncAirportSelect(form, "");
  _applyPrefillToForm(form);
  form.querySelector(".btn-case-cancel").addEventListener("click", () => {
    _editingCaseId = null;
    _prefillCase = null;
    void _renderIdle(onRunComplete);
  });
  form.addEventListener("submit", (ev) => _submitCase(ev, form, onRunComplete));
  return _section("Record new case", form);
}

async function _manageCasesSection(onRunComplete) {
  const holder = document.createElement("div");
  holder.className = "manage-cases-inner";
  holder.innerHTML = `<p class="case-intake-hint">Loading cases…</p>`;
  try {
    const rows = await get("/cases", { cache: "no-store" });
    holder.innerHTML = "";
    if (!Array.isArray(rows) || rows.length === 0) {
      holder.innerHTML = `<p class="case-intake-hint">No cases in the current dataset.</p>`;
      return _section("Manage cases", holder);
    }
    const tbl = document.createElement("table");
    tbl.className = "manage-cases-table";
    tbl.innerHTML =
      "<thead><tr><th>Case</th><th>Patient</th><th>Onset</th><th>Loc</th><th></th></tr></thead>";
    const tb = document.createElement("tbody");
    for (const row of rows) {
      const tr = document.createElement("tr");
      const idShort = String(row.case_id || "").slice(0, 8);
      tr.innerHTML = `<td title="${row.case_id}">${idShort}…</td><td>${_escapeHtml(row.patient_identifier)}</td><td>${String(row.symptom_onset_date || "").slice(0, 10)}</td><td>${row.location_country || ""}${row.location_airport_code ? " · " + row.location_airport_code : ""}</td><td class="manage-cases-actions"></td>`;
      const actions = tr.querySelector(".manage-cases-actions");
      const bEdit = document.createElement("button");
      bEdit.type = "button";
      bEdit.className = "btn-manage btn-edit";
      bEdit.textContent = "Edit";
      bEdit.addEventListener("click", () => {
        _prefillCase = row;
        _editingCaseId = row.case_id;
        void _renderIdle(onRunComplete);
      });
      const bDel = document.createElement("button");
      bDel.type = "button";
      bDel.className = "btn-manage btn-delete";
      bDel.textContent = "Delete";
      bDel.addEventListener("click", () => void _deleteCaseRow(row.case_id, onRunComplete));
      actions.appendChild(bEdit);
      actions.appendChild(bDel);
      tb.appendChild(tr);
    }
    tbl.appendChild(tb);
    holder.appendChild(tbl);
  } catch (e) {
    holder.innerHTML = `<p class="case-intake-hint">Could not load cases (${e && e.message ? e.message : String(e)}).</p>`;
  }
  return _section("Manage cases", holder);
}

async function _deleteCaseRow(caseId, onRunComplete) {
  if (!confirm("Delete this case from the database? This cannot be undone from the UI.")) return;
  try {
    await del(`/cases/${caseId}`);
    if (_editingCaseId === caseId) {
      _editingCaseId = null;
      _prefillCase = null;
    }
    markResearchConsoleCaseDataUpdated();
    if (typeof onRunComplete === "function") onRunComplete();
    await _renderIdle(onRunComplete);
  } catch (err) {
    alert("Delete failed: " + (err && err.message ? err.message : String(err)));
  }
}

async function _submitCase(ev, form, onRunComplete) {
  ev.preventDefault();
  const msg = form.querySelector("#case-intake-msg");
  if (msg) msg.textContent = "";

  const fd = new FormData(form);
  const country = String(fd.get("location_country") || "").trim().toUpperCase();
  if (country.length !== 2) {
    alert("Select a country.");
    return;
  }
  let apt = String(fd.get("location_airport_code") || "").trim().toUpperCase();
  if (apt.length > 0 && apt.length !== 3) {
    alert("Airport must be a valid three-letter code or empty.");
    return;
  }

  const kind = fd.get("observation_kind") || "individual";
  const basePayload = {
    patient_identifier: String(fd.get("patient_identifier") || "").trim(),
    symptom_onset_date: fd.get("symptom_onset_date"),
    hospitalization_date: _optionalDateStr(fd.get("hospitalization_date")),
    location_country: country,
    location_airport_code: apt.length === 3 ? apt : null,
    confirmed_or_suspected: fd.get("confirmed_or_suspected"),
    lab_test_result: fd.get("lab_test_result"),
    contacts: [],
    data_source: String(fd.get("data_source") || "Manual_Form").trim() || "Manual_Form",
    updated_by: "researcher_console",
    observation_kind: kind,
  };

  if (kind === "cohort") {
    basePayload.death_date = null;
    basePayload.cohort_size = Number(fd.get("cohort_size") || 1);
    basePayload.cohort_deaths = Number(fd.get("cohort_deaths") || 0);
    basePayload.report_period_start = _optionalDateStr(fd.get("report_period_start"));
    basePayload.report_period_end = _optionalDateStr(fd.get("report_period_end"));
  } else {
    basePayload.death_date = _optionalDateStr(fd.get("death_date"));
    basePayload.cohort_size = 1;
    basePayload.cohort_deaths = 0;
    basePayload.report_period_start = null;
    basePayload.report_period_end = null;
  }

  if (!basePayload.patient_identifier) {
    alert("Patient ID is required.");
    return;
  }

  try {
    let res;
    if (_editingCaseId) {
      basePayload.updated_reason = "manual_console_edit";
      res = await patch(`/cases/${_editingCaseId}`, basePayload);
    } else {
      basePayload.updated_reason = "manual_console_intake";
      res = await post("/cases", basePayload);
    }
    const wasEdit = !!_editingCaseId;
    const accepted = res.accepted_for_inference === true;
    if (msg) {
      msg.textContent = accepted
        ? wasEdit
          ? "Case updated and accepted for inference."
          : "Case saved and accepted for inference."
        : `Saved (quality ${(res.validation?.quality_score ?? 0).toFixed(2)}).`;
    }
    _editingCaseId = null;
    _prefillCase = null;
    form.reset();
    const ds = form.querySelector('input[name="data_source"]');
    if (ds) ds.value = "Manual_Form";
    _syncAirportSelect(form, "");
    markResearchConsoleCaseDataUpdated();
    if (typeof onRunComplete === "function") onRunComplete();
    await _renderIdle(onRunComplete);
  } catch (err) {
    alert("Could not save case: " + (err && err.message ? err.message : String(err)));
  }
}

async function _syncRunButtonWithActivePipeline() {
  try {
    const res = await get("/inference/jobs/active");
    const job = res.job;
    const busy = job && (job.status === "running" || job.status === "pending");
    _setRunButtonDisabled(!!busy);
    const btn = document.querySelector("#drawer-body .btn-run");
    if (btn) {
      if (busy) {
        btn.title = "A forecast run is already in progress. Wait for it to finish or refresh the page.";
      } else {
        btn.removeAttribute("title");
      }
    }
  } catch (_) {
    _setRunButtonDisabled(false);
    document.querySelector("#drawer-body .btn-run")?.removeAttribute("title");
  }
}

async function _startRun(onRunComplete) {
  const scenarios = [..._selectedScenarios];
  if (!scenarios.length) {
    alert("Select at least one scenario to run a forecast.");
    return;
  }

  _setRunButtonDisabled(true);
  _renderRunning();
  _updateStage(1, "active", { n_cases: "…", quality_mean: "…" });

  let jobId;
  try {
    const result = await post("/inference/run", {
      reason: "manual",
      scenarios,
      n_simulations: _selectedFidelity,
    });
    jobId = result.job_id;
  } catch (err) {
    if (err.status === 409) {
      let aid = err.body?.detail?.active_job_id;
      if (!aid) {
        try {
          const activeRes = await get("/inference/jobs/active");
          const j = activeRes.job;
          if (j?.job_id && (j.status === "running" || j.status === "pending")) {
            aid = j.job_id;
          }
        } catch (_) {
          /* fall through */
        }
      }
      if (aid) {
        _cancelSSE = subscribe(
          aid,
          getSessionToken,
          (event) => _handleEvent(event),
          (event) => _handleComplete(event, aid, onRunComplete),
        );
        return;
      }
    }
    alert("Failed to start run: " + (err && err.message ? err.message : String(err)));
    _setRunButtonDisabled(false);
    await _renderIdle(onRunComplete);
    return;
  }

  _cancelSSE = subscribe(
    jobId,
    getSessionToken,
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
    if (event.status === "complete") {
      _updateStage(2, "done", event);
    } else {
      _updateStage(2, "active", event);
    }
  } else if (event.stage === "simulation") {
    _updateStage(2, "done", _getStageData(2));
    _updateStage(3, event.status === "complete" ? "done" : "active", event);
  } else if (event.stage === "metapop_geo") {
    const st = event.status === "running" ? "active" : "done";
    _updateStage(2, "done", _getStageData(2));
    _updateStage(3, st, { ..._getStageData(3), metapop_geo: event });
  }
}

async function _handleComplete(event, jobId, onRunComplete) {
  _cancelSSE = null;
  _clearInferenceEtaTicker();
  _setRunButtonDisabled(false);

  let record = null;
  try {
    record = await get(`/inference/jobs/${jobId}`);
  } catch (_) {}

  const failed = event?.status === "failed" || event?.error || record?.status === "failed";
  if (failed) {
    _updateStage(4, "failed", {
      job_id: jobId,
      detail: record?.detail || event?.detail || {},
      error: record?.error || event?.error || "Run failed",
    });
    return;
  }

  _updateStage(1, "done", _getStageData(1));
  _updateStage(2, "done", _getStageData(2));
  _updateStage(3, "done", _getStageData(3));
  _updateStage(4, "active", { job_id: jobId, detail: event.detail });

  if (record) {
    _lastRun = record;
    _updateStage(4, "done", record);
  }

  if (typeof onRunComplete === "function") onRunComplete();
}

function _renderRunning() {
  _clearInferenceEtaTicker();
  _stageData = new Map();
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

  const previousData = _getStageData(n);
  const nextData = { ...previousData, ...(data || {}) };
  _stageData.set(n, nextData);
  const wasExpanded = card.classList.contains("expanded") || card.classList.contains("active");
  card.className = `stage-card ${state}`;
  if ((state === "done" || state === "failed") && wasExpanded) {
    card.classList.add("expanded");
  }
  const badge = card.querySelector(".stage-badge");
  if (badge) {
    const badgeInfo = _badgeForState(state);
    badge.className = `stage-badge ${badgeInfo.className}`;
    badge.textContent = badgeInfo.text;
  }

  const body = card.querySelector(".stage-body");
  if (!body) return;
  body.innerHTML = _stageBodyHtml(n, state, nextData);

  // Render mini Plotly charts if needed
  if (n === 2 && state === "active" && data.params) {
    _renderPosteriorChart(nextData);
  }
  if (n === 3 && state === "active" && data.fan_sample) {
    _renderFanChart(nextData);
  }
  if (n === 4 && (state === "active" || state === "done" || state === "failed")) {
    const btn = card.querySelector(".btn-view-dashboard");
    if (btn) btn.addEventListener("click", () => document.getElementById("btn-close-drawer").click());
    const again = card.querySelector(".btn-another-run");
    if (again)
      again.addEventListener("click", () => {
        void _renderIdle(_onRunCompleteCb);
      });
  }
}

function _getStageData(n) {
  return _stageData.get(n) || {};
}

function _badgeForState(state) {
  if (state === "done") return { className: "badge-done", text: "✓ DONE" };
  if (state === "active") return { className: "badge-running", text: "● RUNNING" };
  if (state === "failed") return { className: "badge-failed", text: "FAILED" };
  return { className: "badge-waiting", text: "WAITING" };
}

function _stageBodyHtml(n, state, data) {
  if (n === 4 && state === "done") {
    return `
      <div style="color:var(--ink-muted);font-size:0.78rem;margin-bottom:0.5rem">${_doneSummary(4, data)}</div>
      <div class="result-banner">✓ Forecast complete</div>
      <button type="button" class="btn-view-dashboard">← View updated dashboard</button>
      <button type="button" class="btn-another-run">Run another forecast →</button>
    `;
  }
  if (n === 4 && state === "failed") {
    return `
      <div class="result-banner result-banner-failed">Run failed</div>
      <div style="color:var(--ink-muted);font-size:0.8rem;margin-bottom:0.75rem">${String(data.error || "Unknown error").replace(/</g, "")}</div>
      <button type="button" class="btn-another-run">Back to setup</button>
    `;
  }
  if (state === "done") {
    return `
      <div style="color:var(--ink-muted);font-size:0.78rem;margin-bottom:0.5rem">${_doneSummary(n, data)}</div>
      ${_stageDetailHtml(n, "done", data)}
    `;
  }
  if (state === "pending") return "";

  return _stageDetailHtml(n, state, data);
}

function _stageDetailHtml(n, state, data) {
  if (n === 1) {
    return `<div style="color:var(--teal-light);font-size:0.82rem">
      Cases: <strong>${data.n_cases ?? "…"}</strong> ·
      Mean quality: <strong>${typeof data.quality_mean === "number" ? data.quality_mean.toFixed(2) : "…"}</strong>
    </div>`;
  }
  if (n === 2) {
    if (state === "active" && data.status !== "complete") {
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
    const mg = data.metapop_geo;
    const mTot = mg ? mg.total_runs ?? mg.metapop_n_runs ?? 0 : 0;
    const mDone = mg
      ? mg.runs_completed ?? (mg.status === "complete" ? mTot : 0)
      : 0;
    const mPct = mTot > 0 ? Math.round((mDone / mTot) * 100) : 0;
    let metapopBlock = "";
    if (mg && mg.status === "running" && mTot > 0) {
      metapopBlock = `
          <div style="margin-top:0.55rem;padding-top:0.45rem;border-top:1px solid #1e3530">
            <div style="color:var(--teal-light);font-size:0.78rem;margin-bottom:0.35rem">
              Metapop map kernel · <strong>${mDone.toLocaleString()}</strong> / ${mTot.toLocaleString()} runs (${mPct}%)
              ${mg.elapsed_s != null ? ` · ${mg.elapsed_s}s elapsed` : ""}
            </div>
            <div class="progress-bar"><div class="progress-fill" style="width:${mPct}%"></div></div>
          </div>`;
    } else if (mg && (mg.status === "complete" || mg.status === "failed" || mg.metapop_n_runs != null)) {
      metapopBlock = `<div style="color:var(--teal-light);font-size:0.78rem;margin-top:0.45rem">
            Metapop map kernel:
            <strong>${mg.status === "complete" ? "complete" : mg.status === "failed" ? "failed" : mg.status || "—"}</strong>
            ${mg.metapop_n_runs != null ? ` · ${mg.metapop_n_runs} runs` : mTot ? ` · ${mTot} runs` : ""}
            ${mg.p_transmit_used != null ? ` · p_transmit ${mg.p_transmit_used}` : ""}
            ${mg.error ? `<br/><span style="color:var(--warn-amber,#c9a227)">${String(mg.error).replace(/</g, "")}</span>` : ""}
          </div>`;
    }
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
      ${metapopBlock}
      <div id="fan-chart-${Date.now()}"></div>
    `;
  }
  if (n === 4) {
    return `
      <div class="result-banner">✓ Forecast complete</div>
      <button type="button" class="btn-view-dashboard">← View updated dashboard</button>
      <button type="button" class="btn-another-run">Run another forecast →</button>
    `;
  }
  return "";
}

function _doneSummary(n, data) {
  if (n === 1) return `${data.n_cases ?? "?"} cases · quality ${typeof data.quality_mean === "number" ? data.quality_mean.toFixed(2) : "?"}`;
  if (n === 2) return `Inference complete · R̂ ${data.rhat_max != null ? Number(data.rhat_max).toFixed(3) : "?"} · ${data.divergences ?? "?"} divergences`;
  if (n === 3) {
    const base = `Simulation complete · ${(data.total || data.trajectories || "10,000").toLocaleString()} trajectories`;
    const mg = data.metapop_geo;
    if (mg && mg.status === "complete") {
      return `${base} · metapop geo ${mg.metapop_n_runs ?? "?"} runs`;
    }
    if (mg && mg.status === "failed") {
      return `${base} · metapop geo failed`;
    }
    return base;
  }
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
  const badgeInfo = _badgeForState(state);
  card.innerHTML = `
    <div class="stage-header">
      <span class="stage-title">${title}</span>
      <span class="stage-badge ${badgeInfo.className}">${badgeInfo.text}</span>
    </div>
    <div class="stage-body"></div>
  `;
  card.querySelector(".stage-header").addEventListener("click", () => {
    if (!card.classList.contains("done") && !card.classList.contains("failed")) return;
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
        const wasSelected = pill.classList.contains("selected");
        if (wasSelected && group.querySelectorAll(".pill.selected").length === 1) {
          return;
        }
        const nowSelected = !wasSelected;
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
