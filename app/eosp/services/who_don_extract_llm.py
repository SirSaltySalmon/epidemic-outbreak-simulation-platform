"""LLM-backed extraction for WHO DON narrative text (OpenAI-compatible Chat Completions API)."""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from typing import Any

from eosp.core.settings import Settings

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You extract structured epidemic counts from WHO Disease Outbreak News text. "
    "Reply with a single JSON object only, no markdown. "
    'Fields: "cohort_size" (integer, total cases in the main situation if stated; '
    "otherwise lab-confirmed plus suspected if both are given as digits; minimum 1 if any cases exist), "
    '"cohort_deaths" (integer, 0 if none stated), '
    '"countries" (array of ISO 3166-1 alpha-2 codes or common English country names mentioned), '
    '"symptom_onset_date" (string YYYY-MM-DD best representative report date from the passage, or null). '
    "If the text does not support a reliable total case count, use cohort_size null."
)


def llm_extract_who_don_fields(focus_text: str, settings: Settings) -> dict[str, Any] | None:
    """Return a parsed JSON dict or None on failure / skip."""

    key = (settings.who_don_llm_api_key or "").strip()
    if not key:
        return None

    clipped = focus_text[:8000] if len(focus_text) > 8000 else focus_text
    payload = {
        "model": settings.who_don_llm_model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {
                "role": "user",
                "content": "Extract counts from this DON excerpt:\n\n" + clipped,
            },
        ],
    }
    body = json.dumps(payload).encode("utf-8")
    base = settings.who_don_llm_base_url.rstrip("/")
    url = f"{base}/chat/completions"
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=float(settings.who_don_llm_timeout_seconds)) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.warning("WHO DON LLM request failed: %s", exc)
        return None

    try:
        top = json.loads(raw)
        content = top["choices"][0]["message"]["content"]
    except (KeyError, IndexError, json.JSONDecodeError, TypeError) as exc:
        logger.warning("WHO DON LLM parse envelope failed: %s", exc)
        return None

    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning("WHO DON LLM returned non-JSON: %s…", text[:120])
        return None
