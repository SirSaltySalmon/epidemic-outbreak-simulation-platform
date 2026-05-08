"""Heuristic + optional LLM extraction of aggregate case counts from WHO DON HTML.

Used when automated ingest is enabled or for tests; **operators normally enter cohorts manually.**
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any
from uuid import uuid4

from eosp.core.models import (
    CaseCreate,
    CaseStatus,
    LabResult,
    ObservationKind,
)
from eosp.core.settings import Settings, get_settings
from eosp.services.validation import VALID_COUNTRIES
from eosp.services.who_don_poller import _normalize_who_html

_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

_COUNTRY_ALIASES: tuple[tuple[str, str], ...] = (
    ("south africa", "ZA"),
    ("switzerland", "CH"),
    ("netherlands", "NL"),
    ("the netherlands", "NL"),
    ("spain", "ES"),
    ("united kingdom", "GB"),
    ("uk", "GB"),
    ("cape verde", "CV"),
    ("cabo verde", "CV"),
)

_DIGIT_WORDS: dict[str, int] = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}


def _int_word_or_digit(token: str) -> int | None:
    t = token.lower().strip()
    if t in _DIGIT_WORDS:
        return _DIGIT_WORDS[t]
    if t.isdigit():
        return int(t)
    return None

_FOCUS_MARKERS: tuple[str, ...] = (
    "situation at a glance",
    "the situation",
    "disease outbreak news",
    "as of ",
    "reported to",
    "laboratory",
)


def _detect_countries(text: str) -> list[str]:
    found: list[str] = []
    lower = text.lower()
    for alias, code in _COUNTRY_ALIASES:
        if alias in lower and code not in found:
            found.append(code)
    for code in sorted(VALID_COUNTRIES):
        if re.search(rf"\b{re.escape(code.lower())}\b", lower) and code not in found:
            found.append(code)
    return found


def _pick_country(codes: list[str]) -> str:
    if len(codes) == 1:
        return codes[0]
    if len(codes) > 1:
        return "CV"
    return "CV"


def _extract_report_date(text: str, fallback: date) -> date:
    m = re.search(r"\b(\d{1,2})\s+([a-z]+)\s+(\d{4})\b", text.lower())
    if m:
        d, mon_s, y = int(m.group(1)), m.group(2), int(m.group(3))
        mon = _MONTHS.get(mon_s)
        if mon is not None:
            try:
                return date(y, mon, d)
            except ValueError:
                pass
    m2 = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
    if m2:
        try:
            return date(int(m2.group(1)), int(m2.group(2)), int(m2.group(3)))
        except ValueError:
            pass
    return fallback


def _first_int_after(patterns: list[str], text: str) -> int | None:
    for pat in patterns:
        m = re.search(pat, text, flags=re.I)
        if m:
            return int(m.group(1))
    return None


def _extraction_focus(normalized_full: str, max_chars: int = 9000) -> str:
    """Narrow normalized text to the epidemic narrative (drop global nav noise)."""

    lower = normalized_full.lower()
    best = len(normalized_full)
    start = 0
    for marker in _FOCUS_MARKERS:
        i = lower.find(marker)
        if 0 <= i < best:
            best = i
            start = i
    m_body = re.search(r"\d+\s+(?:laboratory[- ]?|confirmed\b|cases\b)", lower)
    m_anchor = re.search(r"\bon \d{1,2}\s+[a-z]+\s+\d{4}\b", lower)
    if m_anchor and m_body and m_anchor.start() > m_body.start():
        m_anchor = None
    if m_anchor and m_anchor.start() < best:
        best = m_anchor.start()
        start = m_anchor.start()
    chunk = normalized_full[start : start + max_chars]
    return chunk if chunk.strip() else normalized_full[:max_chars]


def _regex_deaths(focus_lower: str) -> int:
    m = re.search(r"including\s+(\d+)\s+deaths?", focus_lower)
    if m:
        return int(m.group(1))
    m = re.search(
        r"including\s+(one|two|three|four|five|six|seven|eight|nine|ten)\s+deaths?",
        focus_lower,
    )
    if m:
        v = _int_word_or_digit(m.group(1))
        if v is not None:
            return v
    m = re.search(r"(\d+)\s+deaths?\s+have\s+been\s+(?:reported|recorded)", focus_lower)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\s+deaths?\b", focus_lower)
    if m:
        return int(m.group(1))
    m = re.search(r"deaths?\s*[:\s]+\s*(\d+)", focus_lower)
    if m:
        return int(m.group(1))
    return 0


def _regex_cohort_from_focus(focus: str, fallback: date) -> tuple[int, int, date] | None:
    """Deterministic path on the focus window only."""

    low = focus.lower()
    deaths = _regex_deaths(low)

    total: int | None = None
    m_cases_paren = re.search(r"(\d+)\s+cases\s*\(", low)
    if m_cases_paren:
        total = int(m_cases_paren.group(1))

    if total is None:
        wn = r"(\d+|one|two|three|four|five|six|seven|eight|nine|ten)"
        lab_m = re.search(rf"\b{wn}\s+laboratory[- ]?confirmed", low)
        sus_m = re.search(rf"\b{wn}\s+suspected", low)
        lab_n = _int_word_or_digit(lab_m.group(1)) if lab_m else None
        sus_n = _int_word_or_digit(sus_m.group(1)) if sus_m else None
        if lab_n is not None and sus_n is not None:
            total = lab_n + sus_n
        elif lab_n is not None:
            total = lab_n

    if total is None:
        total = _first_int_after(
            [
                r"(\d+)\s+laboratory[- ]?confirmed",
                r"(\d+)\s+confirmed\s+cases",
                r"(\d+)\s+confirmed\b",
                r"(\d+)\s+cases\s+have\s+been\s+(?:reported|identified)",
                r"(\d+)\s+cases\b",
            ],
            low,
        )

    if total is None or total <= 0:
        return None

    onset = _extract_report_date(focus, fallback)
    return total, deaths, onset


def _sanity_cohort(n: int, deaths: int, cap: int) -> bool:
    if n < 1 or n > cap:
        return False
    if deaths < 0 or deaths > n:
        return False
    return True


def _parse_iso_date(s: Any) -> date | None:
    if not s or not isinstance(s, str):
        return None
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s.strip())
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _countries_from_llm(raw: Any, focus: str) -> list[str]:
    merged = _detect_countries(focus)
    if not isinstance(raw, list):
        return merged
    alias_to_code = {a: c for a, c in _COUNTRY_ALIASES}
    for item in raw:
        if not isinstance(item, str):
            continue
        t = item.strip()
        if len(t) == 2 and t.upper() in VALID_COUNTRIES and t.upper() not in merged:
            merged.append(t.upper())
            continue
        tl = t.lower()
        if tl in alias_to_code and alias_to_code[tl] not in merged:
            merged.append(alias_to_code[tl])
    return merged


def detected_who_don_countries(html: str) -> list[str]:
    """ISO-ish country hints found in DON body text after normalization."""

    normalized = _normalize_who_html(html)
    if not normalized:
        return []
    focus = _extraction_focus(normalized)
    return _detect_countries(focus)


def extract_who_don_cohort_payloads(
    html: str,
    *,
    feed_key: str,
    don_item_id: str | None = None,
    report_fallback: date | None = None,
    settings: Settings | None = None,
) -> list[CaseCreate]:
    """Parse DON HTML into at most one aggregate :class:`CaseCreate`.

    ``settings.who_don_extract_mode``: ``regex`` | ``llm`` | ``hybrid`` (default).
    Hybrid tries regex on a focus window, then optional LLM when regex fails sanity or is absent.
    """

    cfg = settings or get_settings()
    normalized = _normalize_who_html(html)
    if not normalized:
        return []

    focus = _extraction_focus(normalized)
    fallback = report_fallback or date(2026, 5, 4)
    cap = max(100, int(cfg.who_don_extract_max_cohort))

    tuple_result: tuple[int, int, date] | None = None
    ing_reason = "WHO_DON_cohort_ingest"
    countries_override: list[str] | None = None

    mode = cfg.who_don_extract_mode

    if mode in ("regex", "hybrid"):
        tup = _regex_cohort_from_focus(focus, fallback)
        if tup is not None and _sanity_cohort(tup[0], tup[1], cap):
            tuple_result = tup

    if tuple_result is None and mode in ("llm", "hybrid"):
        from eosp.services.who_don_extract_llm import llm_extract_who_don_fields

        blob = llm_extract_who_don_fields(focus, cfg)
        if blob:
            cs = blob.get("cohort_size")
            cd = blob.get("cohort_deaths")
            if isinstance(cs, int) and isinstance(cd, int) and _sanity_cohort(cs, cd, cap):
                onset = _parse_iso_date(blob.get("symptom_onset_date")) or _extract_report_date(focus, fallback)
                tuple_result = (cs, cd, onset)
                ing_reason = "WHO_DON_cohort_ingest_llm"
                countries_override = _countries_from_llm(blob.get("countries"), focus)

    if tuple_result is None:
        return []

    n_confirmed, deaths, onset = tuple_result
    countries = countries_override if countries_override is not None else _detect_countries(focus)
    country = _pick_country(countries)

    if don_item_id:
        ext_key = f"{feed_key}:{don_item_id}:aggregate"
        patient_identifier = f"who_don_{feed_key}_{don_item_id}_cohort"
    else:
        ext_key = f"{feed_key}:aggregate"
        patient_identifier = f"who_don_{feed_key}_cohort"
    return [
        CaseCreate(
            case_id=uuid4(),
            patient_identifier=patient_identifier,
            symptom_onset_date=onset,
            location_country=country,
            location_airport_code=None,
            confirmed_or_suspected=CaseStatus.CONFIRMED,
            lab_test_result=LabResult.NOT_TESTED,
            data_source="WHO_DON",
            observation_kind=ObservationKind.COHORT,
            cohort_size=n_confirmed,
            cohort_deaths=deaths,
            external_observation_key=ext_key,
            updated_by="who_don_poller",
            updated_reason=ing_reason,
        )
    ]
