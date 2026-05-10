from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DRAWER_JS = ROOT / "app" / "eosp" / "static" / "js" / "drawer.js"
DRAWER_CSS = ROOT / "app" / "eosp" / "static" / "css" / "drawer.css"
API_JS = ROOT / "app" / "eosp" / "static" / "js" / "api.js"
MAIN_JS = ROOT / "app" / "eosp" / "static" / "js" / "main.js"


def test_stage_cards_do_not_capture_initial_state_for_toggle():
    source = DRAWER_JS.read_text(encoding="utf-8")

    assert 'if (state !== "done") return;' not in source
    assert "classList.contains(\"done\")" in source


def test_inference_terminal_events_render_done_not_running():
    source = DRAWER_JS.read_text(encoding="utf-8")

    assert 'event.stage === "inference"' in source
    assert 'event.status === "complete"' in source
    assert "using stored posterior" not in source
    assert '_updateStage(2, "done", event)' in source


def test_simulation_complete_event_renders_stage_done_with_details():
    source = DRAWER_JS.read_text(encoding="utf-8")

    assert 'event.status === "complete" ? "done" : "active"' in source
    assert '_stageDetailHtml(n, "done", data)' in source
    assert "Hub timeline" in source


def test_progress_stream_reports_close_before_completion_via_fetch_reader():
    source = (ROOT / "app" / "eosp" / "static" / "js" / "jobs.js").read_text(encoding="utf-8")

    assert "reader.read()" in source
    assert "ac.abort()" in source
    assert "Progress stream closed before completion" in source


def test_completion_defensively_marks_all_prior_stages_done():
    source = DRAWER_JS.read_text(encoding="utf-8")

    assert '_updateStage(1, "done"' in source
    assert '_updateStage(2, "done"' in source
    assert '_updateStage(3, "done"' in source


def test_stage_body_can_scroll_large_pipeline_details():
    source = DRAWER_CSS.read_text(encoding="utf-8")

    assert ".stage-body" in source
    assert "overflow-y: auto" in source
    assert "max-height:" in source


def test_console_case_refresh_bypasses_public_get_cache_after_mutations():
    api_source = API_JS.read_text(encoding="utf-8")
    drawer_source = DRAWER_JS.read_text(encoding="utf-8")
    main_source = MAIN_JS.read_text(encoding="utf-8")

    assert "export async function get(path, options = {})" in api_source
    assert "cache: options.cache || \"default\"" in api_source
    assert 'get("/cases", { cache: "no-store" })' in drawer_source
    assert 'get(_DASHBOARD_BOOTSTRAP, { cache: "no-store" })' in main_source
