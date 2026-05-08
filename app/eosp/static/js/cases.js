/**
 * Scrollable case line list (GET /cases).
 */

/** @param {string} iso */
function _dateLabel(iso) {
  if (iso == null || String(iso).trim() === "") return "—";
  const s = String(iso);
  return s.length >= 10 ? s.slice(0, 10) : s;
}

/**
 * Newest activity first: symptom onset desc, then ingestion.
 * @param {any[]} cases
 */
function _sortForDisplay(cases) {
  return [...cases].sort((a, b) => {
    const oa = String(a.symptom_onset_date || "");
    const ob = String(b.symptom_onset_date || "");
    if (ob !== oa) return ob.localeCompare(oa);
    const ia = String(a.ingestion_timestamp || "");
    const ib = String(b.ingestion_timestamp || "");
    return ib.localeCompare(ia);
  });
}

/** @param {any} c */
function _locationLine(c) {
  const cc = (c.location_country || "—").toUpperCase();
  const ap = c.location_airport_code ? ` · ${String(c.location_airport_code).toUpperCase()}` : "";
  return cc + ap;
}

/** @param {any} c */
function _titleLine(c) {
  const id = c.patient_identifier || "—";
  const kind = c.observation_kind === "cohort" ? ` · cohort n=${c.cohort_size ?? "?"}` : "";
  return id + kind;
}

/** @param {any} c */
function _metaLine(c) {
  const src = c.data_source || "—";
  const shortSrc = src.length > 36 ? src.slice(0, 33) + "…" : src;
  const vid = c.case_id ? String(c.case_id).slice(0, 8) : "";
  return vid ? `${shortSrc} · id ${vid}…` : shortSrc;
}

/**
 * @param {any[] | null} cases
 * @param {string} [errorMessage]
 */
export function applyCasesList(cases, errorMessage) {
  const countEl = document.getElementById("cases-list-count");
  const scrollEl = document.getElementById("cases-scroll");
  const emptyEl = document.getElementById("cases-empty");
  if (!scrollEl) return;

  scrollEl.innerHTML = "";

  if (errorMessage) {
    if (countEl) countEl.textContent = "";
    if (emptyEl) {
      emptyEl.hidden = false;
      emptyEl.textContent = errorMessage;
      emptyEl.classList.add("cases-empty--error");
    }
    return;
  }

  if (!cases || cases.length === 0) {
    if (countEl) countEl.textContent = "0 rows";
    if (emptyEl) {
      emptyEl.hidden = false;
      emptyEl.textContent = "No case rows in the database yet.";
      emptyEl.classList.remove("cases-empty--error");
    }
    return;
  }

  if (emptyEl) {
    emptyEl.hidden = true;
    emptyEl.classList.remove("cases-empty--error");
  }
  if (countEl) countEl.textContent = `${cases.length} row${cases.length === 1 ? "" : "s"}`;

  const sorted = _sortForDisplay(cases);
  for (const c of sorted) {
    const row = document.createElement("div");
    row.className = "case-row";
    row.setAttribute("role", "listitem");

    const status = String(c.confirmed_or_suspected || "").toLowerCase();
    const pill = document.createElement("span");
    pill.className = "case-status case-status--" + (status === "suspected" ? "suspected" : "confirmed");
    pill.textContent = status === "suspected" ? "Suspected" : "Confirmed";

    const main = document.createElement("div");
    main.className = "case-row-main";
    const t = document.createElement("div");
    t.className = "case-row-title";
    t.textContent = _titleLine(c);
    const sub = document.createElement("div");
    sub.className = "case-row-sub";
    sub.textContent = _metaLine(c);
    main.appendChild(t);
    main.appendChild(sub);

    const side = document.createElement("div");
    side.className = "case-row-side";
    const loc = document.createElement("div");
    loc.className = "case-row-loc";
    loc.textContent = _locationLine(c);
    const onset = document.createElement("div");
    onset.className = "case-row-onset";
    onset.textContent = "Onset " + _dateLabel(c.symptom_onset_date);
    if (c.death_date) {
      const d = document.createElement("div");
      d.className = "case-row-death";
      d.textContent = "Death " + _dateLabel(c.death_date);
      side.appendChild(loc);
      side.appendChild(onset);
      side.appendChild(d);
    } else {
      side.appendChild(loc);
      side.appendChild(onset);
    }

    row.appendChild(pill);
    row.appendChild(main);
    row.appendChild(side);
    scrollEl.appendChild(row);
  }
}
