"""Disease progression draws (gamma / Weibull) for synthetic agents."""

from __future__ import annotations

import math
from typing import Any


def _weibull_scale_from_mean(mean: float, shape: float) -> float:
    return float(mean) / math.gamma(1.0 + 1.0 / float(shape))


def sample_e_and_p_remaining(
    rng: Any,
    *,
    incubation_mean: float,
    gamma_shape: float,
    mean_scale: float,
    p_share_mean: float,
) -> tuple[int, int]:
    """Split total incubation into E and P segment day counters (≥1 each when possible)."""

    total = max(1.0, float(incubation_mean) * float(mean_scale))
    theta = total / float(gamma_shape)
    draw = float(rng.gamma(float(gamma_shape), theta))
    total_days = max(2, int(round(draw)))
    p_target = max(1.0, min(float(p_share_mean) * total_days, total_days - 1.0))
    p_rem = max(1, int(round(p_target)))
    e_rem = total_days - p_rem
    if e_rem < 1:
        e_rem = 1
        p_rem = max(1, total_days - 1)
    return e_rem, p_rem


def sample_weibull_duration(
    rng: Any,
    *,
    mean: float,
    shape: float,
) -> int:
    scale = _weibull_scale_from_mean(mean, shape)
    return max(1, int(rng.weibull(float(shape)) * scale))
