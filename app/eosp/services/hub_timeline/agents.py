"""Agent kinds, compartments, and synthetic airport mobility."""

from __future__ import annotations

from enum import IntEnum, StrEnum
from typing import Any

from eosp.services.hub_timeline.config import HubTimelineSimulatorConfig


class AgentKind(StrEnum):
    SYNTHETIC = "synthetic"
    FIXED = "fixed"


class AgentState(IntEnum):
    """Match retired ABM integer codes where practical (see docs/ABM_RETIREMENT.md)."""

    E = 1
    I = 2
    R = 3
    D = 4
    P = 5
    H = 6


def step_mobility_synthetic(
    rng: Any,
    *,
    iata_current: str,
    iata_home: str,
    away: bool,
    outbound: dict[str, dict[str, int]],
    cfg: HubTimelineSimulatorConfig,
) -> tuple[str, bool]:
    """Return ``(new_iata, new_away)`` for one calendar day (synthetic only)."""

    cur = str(iata_current).upper()
    home = str(iata_home).upper()
    if not away:
        if cur != home:
            return cur, True
        if rng.random() < cfg.p_stay_when_at_home:
            return cur, False
        outs = outbound.get(cur)
        if not outs:
            return cur, False
        pairs = sorted(outs.items(), key=lambda t: t[0])
        import numpy as np

        w = np.array([float(x[1]) for x in pairs], dtype=float)
        pick = int(rng.choice(len(pairs), p=w / w.sum()))
        dest = str(pairs[pick][0])
        if dest == home:
            return cur, False
        return dest, True
    # away: return home or stay at foreign (never chain a third hub)
    if rng.random() < cfg.p_return_when_away:
        return home, False
    return cur, True
