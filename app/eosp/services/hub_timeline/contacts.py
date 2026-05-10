"""Contact-count draws (negative binomial) and load-scaled transmission hazard."""

from __future__ import annotations

import math
from typing import Any


def draw_contact_count(rng: Any, mu: float, r_disp: float) -> int:
    """NB2 counts with mean ``mu`` and dispersion ``r_disp`` (NumPy parameterization)."""

    if r_disp >= 1e9 or mu <= 0:
        return int(rng.poisson(max(mu, 0.0)))
    p = r_disp / (r_disp + mu)
    return int(rng.negative_binomial(r_disp, p))


def transmission_probability(
    p_transmit: float,
    h2h: float,
    load_at_hub: float,
    *,
    alpha_load: float,
) -> float:
    eff = 1.0 + alpha_load * max(0.0, load_at_hub)
    return min(1.0 - math.exp(-p_transmit * h2h * eff), 0.999999)
