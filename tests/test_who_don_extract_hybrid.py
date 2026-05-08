"""Hybrid WHO DON extract: regex focus window + optional LLM."""

from __future__ import annotations

from datetime import date

from eosp.core.settings import Settings
from eosp.services.who_don_extract import extract_who_don_cohort_payloads


def test_hybrid_regex_sums_lab_and_suspected_when_no_digit_total() -> None:
    html = """<article>On 4 May 2026, seven cases (two laboratory-confirmed cases of hantavirus
    and five suspected cases) have been identified, including three deaths.</article>"""
    cfg = Settings(
        who_don_extract_mode="regex",
        who_don_extract_max_cohort=500,
    )
    rows = extract_who_don_cohort_payloads(
        html,
        feed_key="t",
        don_item_id="DONx",
        report_fallback=date(2026, 5, 1),
        settings=cfg,
    )
    assert len(rows) == 1
    assert rows[0].cohort_size == 7
    assert rows[0].cohort_deaths == 3


def test_hybrid_llm_fallback_when_regex_insane(monkeypatch) -> None:
    html = "<article>Everything is fine. 9000 cases. 9999 deaths.</article>"
    cfg = Settings(who_don_extract_mode="hybrid", who_don_extract_max_cohort=500)

    def fake_llm(_focus: str, _settings: Settings):
        return {
            "cohort_size": 7,
            "cohort_deaths": 1,
            "countries": ["ES"],
            "symptom_onset_date": "2026-05-04",
        }

    monkeypatch.setattr(
        "eosp.services.who_don_extract_llm.llm_extract_who_don_fields",
        fake_llm,
    )
    rows = extract_who_don_cohort_payloads(
        html,
        feed_key="t",
        settings=cfg,
    )
    assert len(rows) == 1
    assert rows[0].cohort_size == 7
    assert rows[0].cohort_deaths == 1
    assert rows[0].updated_reason == "WHO_DON_cohort_ingest_llm"
