# EOSP agent notes

## Hub timeline simulator

Forward simulation lives in [`app/eosp/services/hub_timeline/`](app/eosp/services/hub_timeline/). Design spec: [`docs/superpowers/specs/2026-05-10-eosp-hub-timeline-simulator-design.md`](docs/superpowers/specs/2026-05-10-eosp-hub-timeline-simulator-design.md).

## Narrative index case (cruise skip)

To exclude one `CaseRecord` from the simulation anchor and from forcing, set **`EOSP_HUB_INDEX_CASE_ID`** in the environment to that row’s UUID string (see [`app/eosp/core/settings.py`](app/eosp/core/settings.py) field `hub_index_case_id`).

## Retired ABM

Legacy vector ABM notes: [`docs/ABM_RETIREMENT.md`](docs/ABM_RETIREMENT.md).
