# WHO DON cohort ingestion — design spec

## Operational status — automated polling deprecated

**Background WHO DON fetch + ingest** (hub crawl, fingerprints, `run_who_don_hub_ingest_cycle` in `main.py`) is **deprecated** for day-to-day use. Operators should **manually enter** outbreak totals and geography via the normal case ingestion flows. The implementation remains in the codebase and can be re-enabled with `EOSP_WHO_DON_POLL_ENABLED=true` for experiments. **Default is off.**

The **cohort observation model** (`observation_kind`, `cohort_size`, `external_observation_key`, etc.) stays in force for manually created WHO_DON-style rows.

## Goal

Ingest World Health Organization Disease Outbreak News (DON) **aggregate** counts into EOSP as **cohort observations** (many people per row), and drive inference/ensemble using **person-equivalent** totals rather than row counts.

## Data model

- **`observation_kind`**: `individual` (default) | `cohort`.
- **`cohort_size`**: integer ≥ 1; person-equivalent count for onsets attributed to `symptom_onset_date` (MVP: single representative date; optional period fields for future daily spread).
- **`cohort_deaths`**: integer ≥ 0; deaths attributed to this **cohort** row (do not use `death_date` for multi-person rows).
- **`report_period_start` / `report_period_end`**: optional; MVP parser may leave null.
- **`external_observation_key`**: optional unique string for **idempotent upsert** (WHO_DON replaces the same logical observation when the DON page updates). For hub → item crawl, keys are scoped per DON article, e.g. `{base_feed_key}:{don_item_id}:aggregate` (e.g. `who_don_2026_e000227:2026-DON600:aggregate`).

**Constraints**

- `individual` rows MUST have `cohort_size == 1`. Deaths use `death_date` and `cohort_deaths == 0`.
- `cohort` rows SHOULD use `death_date is None` when `cohort_deaths` captures fatalities.

## Double counting

- WHO_DON cohort rows use `external_observation_key` scoped by `EOSP_WHO_DON_FEED_KEY` and the **DON item id** parsed from `…/disease-outbreak-news/item/{id}`. Same key → **update in place** (sizes/deaths), not append. Distinct DON articles create distinct cohort rows.
- Operators SHOULD avoid overlapping manual line-list cases for the same jurisdiction/time window as an official aggregate; conflict resolution is **operational** (out of scope for MVP code).

## Parser (MVP + hybrid v1)

- **Modes** (`EOSP_WHO_DON_EXTRACT_MODE`): `regex` | `llm` | `hybrid` (default). Hybrid runs a **focus-window** regex (lab + suspected word/digit sums, word deaths, stricter ordering), sanity bounds (`EOSP_WHO_DON_EXTRACT_MAX_COHORT`), then an **OpenAI-compatible** Chat Completions JSON call if regex fails or is insane.
- **LLM** (optional): `EOSP_WHO_DON_LLM_API_KEY`, `EOSP_WHO_DON_LLM_BASE_URL`, `EOSP_WHO_DON_LLM_MODEL`, `EOSP_WHO_DON_LLM_TIMEOUT_SECONDS`. Successful LLM rows set `updated_reason` = `WHO_DON_cohort_ingest_llm`.
- **Limitation**: ambiguous narratives may still collapse geography to a **single** cohort row (`CV` when multiple ISO2 hits). Low confidence / invalid JSON / HTTP errors → no rows.

## Polling and triggers (hub + item crawl) — optional, deprecated by default

`EOSP_WHO_DON_URL` is the **emergency event hub** (`…/emergencies/emergency-events/item/{event-id}`). That page lists links to **Disease Outbreak News items** (`…/emergencies/disease-outbreak-news/item/{don-id}`), where narrative text and counts live.

**This automated loop is off unless explicitly enabled.** Steps when `EOSP_WHO_DON_POLL_ENABLED=true`:

1. **Hub fetch:** `GET` hub HTML; fingerprint with `record_external_feed_poll` under key `{EOSP_WHO_DON_FEED_KEY}:hub`. The first successful hub poll establishes a **baseline only** (`treat_first_poll_as_changed=False`): no ingest from hub HTML.
2. **Discover:** Parse hub HTML for DON item URLs; order by list date (newest first) when parsable, else document order. Policy `EOSP_WHO_DON_ITEM_POLICY`: `latest_only` (default) processes the newest link only; `all_discovered` processes every distinct item on the hub.
3. **Item fetch:** For each selected item, `GET` item HTML; fingerprint under `{EOSP_WHO_DON_FEED_KEY}:item:{don-id}`. **First poll for that item counts as a content change** (`treat_first_poll_as_changed=True`) so the initial article is ingested without waiting for an edit.
4. On item fingerprint change (including first poll): parse item HTML → `upsert_external_observation` with `external_observation_key` = `{base}:{don-id}:aggregate`.
5. **Full refresh** runs **only if** at least one upsert in the cycle reported **data** mutation (insert or material field update).

## Inference and simulation

- Daily onset vector: add **`cohort_size`** to the bucket for `symptom_onset_date` (within window).
- **`InferenceResult.n_cases`**: sum of `cohort_size` (observed persons).
- **Diagnostics**: `data_quality_mean` = validation-score weighted mean by `cohort_size`.
- **Jobs / forecast** seed helpers use person-equivalent totals and cohort deaths instead of `len(cases)` / per-row death flags alone.

## API / summary

- **`CaseSummary`**: confirmed/suspected/deaths aggregate by summing **`cohort_size`** / **`cohort_deaths`** / individual `death_date` consistently; `data_sources` counts person-equivalent units.

## Testing

- Offline golden HTML snippets for the extractor.
- Unit tests for `daily_onsets_from_cases`, validation duplicates for cohort keys, repository upsert idempotency.
- No live WHO HTTP in CI.

---

## Implementation checklist (writing-plans)

1. Alembic migration: new `case_records` columns + unique nullable `external_observation_key`.
2. Pydantic models + repository row mapping + `case_summary` math.
3. `case_statistics` helpers (person totals, deaths, weighted validation mean).
4. `validation.py` cohort dedupe rules.
5. `inference.py` / `jobs.py` / `forecast.py` / `geo.py` consumer updates.
6. `who_don_discover.py`, `who_don_hub_cycle.py` (**deprecated default**); extend `who_don_extract` / `who_don_ingest` for per-item keys; `record_external_feed_poll(treat_first_poll_as_changed=…)`; `main.py` hub loop (off unless `EOSP_WHO_DON_POLL_ENABLED=true`).
7. Tests and seed data compatibility (defaults on existing records).
