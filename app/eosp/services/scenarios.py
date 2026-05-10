"""Scenario specifications loaded from app/eosp/data/scenarios.json (FR-3.3).

Replaces the hardcoded ``SCENARIO_CONFIG`` dict that the legacy analytic
forecaster used. The same name ``SCENARIO_CONFIG`` is re-exported so that
``app/eosp/api/routes.py`` and existing tests keep working without import
changes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from eosp.services.network import ContactNetwork, apply_modifications


@dataclass
class ScenarioSpec:
    name: str
    description: str = ""
    network_modifications: dict[str, Any] = field(default_factory=dict)
    parameter_overrides: dict[str, Any] = field(default_factory=dict)
    public_label: str = ""
    similar_to: str = ""
    technical_explanation: str = ""
    ui_color: str = ""

    def applied_to(self, network: ContactNetwork) -> ContactNetwork:
        if not self.network_modifications:
            return network
        return apply_modifications(network, self.network_modifications)

    def apply_to_parameters(self, params: Mapping[str, float]) -> dict[str, float]:
        result: dict[str, float] = dict(params)
        if not self.parameter_overrides:
            return result
        scale = float(self.parameter_overrides.get("p_transmit_scale", 1.0))
        if scale != 1.0:
            result["p_transmit"] = float(result.get("p_transmit", 0.08)) * scale
        for key, value in self.parameter_overrides.items():
            if key in {"p_transmit_scale"}:
                continue
            if key in {"evacuation_delay_days"}:
                result[key] = float(value)
            elif isinstance(value, (int, float)):
                result[key] = float(value)
        return result


def _default_path() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "scenarios.json"


def load_scenarios(path: Path | str | None = None) -> dict[str, ScenarioSpec]:
    payload = json.loads((Path(path) if path else _default_path()).read_text(encoding="utf-8"))
    scenarios: dict[str, ScenarioSpec] = {}
    for entry in payload["scenarios"]:
        nm = entry["name"]
        spec = ScenarioSpec(
            name=nm,
            description=entry.get("description", ""),
            network_modifications=entry.get("network_modifications", {}) or {},
            parameter_overrides=entry.get("parameter_overrides", {}) or {},
            public_label=entry.get("public_label") or nm.replace("_", " ").title(),
            similar_to=entry.get("similar_to", ""),
            technical_explanation=entry.get("technical_explanation", ""),
            ui_color=entry.get("ui_color", ""),
        )
        scenarios[spec.name] = spec
    return scenarios


SCENARIO_CONFIG: dict[str, ScenarioSpec] = load_scenarios()


def scenario_catalog_for_api() -> list[dict[str, str | None]]:
    """Ordered catalog for dashboard bootstrap (insertion order matches scenarios.json)."""

    out: list[dict[str, str | None]] = []
    for spec in SCENARIO_CONFIG.values():
        out.append(
            {
                "id": spec.name,
                "description": spec.description,
                "public_label": spec.public_label,
                "similar_to": spec.similar_to,
                "technical_explanation": spec.technical_explanation,
                "ui_color": spec.ui_color or None,
            }
        )
    return out


def get_scenario(name: str) -> ScenarioSpec:
    if name not in SCENARIO_CONFIG:
        raise KeyError(name)
    return SCENARIO_CONFIG[name]
