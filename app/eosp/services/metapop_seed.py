"""Initial SEIR state for metapopulation runs from ship seed + medevac flight legs.

Flight import uses small fixed fractions of passenger counts: 15% latent (E),
5% infectious (I), a simple v1 prior for pathogens carried off-ship.
"""

from __future__ import annotations

import logging

import numpy as np

from eosp.services.mobility import MobilitySchedule

logger = logging.getLogger(__name__)

# Fractions of medevac passengers allocated to latent vs infectious compartments.
_FLIGHT_FRAC_E = 0.15
_FLIGHT_FRAC_I = 0.05

# Ship outbreak mass split between E and I (80/20).
_SHIP_FRAC_E = 0.8
_SHIP_FRAC_I = 0.2


def _iata_from_destination(destination: str) -> str:
    if "_" in destination:
        return destination.rsplit("_", 1)[-1]
    return destination


def _ship_patch_index(patch_ids: tuple[str, ...]) -> int:
    """Prefer NSEED, then SHIP, else the first patch."""
    for marker in ("NSEED", "SHIP"):
        try:
            return patch_ids.index(marker)
        except ValueError:
            continue
    return 0


def _patch_index_for_flight_destination(
    patch_ids: tuple[str, ...], destination: str
) -> int | None:
    iata = _iata_from_destination(destination)
    for i, pid in enumerate(patch_ids):
        if pid == destination:
            return i
        if pid.upper() == iata.upper():
            return i
    return None


def build_initial_metapop_state(
    *,
    schedule: MobilitySchedule,
    network_spec: dict,
    ship_outbreak_mass: float,
    default_patch_pop: float = 10_000.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Build S,E,I,R vectors aligned with ``schedule.patch_ids``.

    Each patch starts at ``default_patch_pop`` susceptible. The ship outbreak
    removes ``ship_outbreak_mass`` susceptibles at the seed patch (see
    :func:`_ship_patch_index` for precedence) and places it in E/I. Each
    flight in ``network_spec["flights"]`` moves a fraction of ``n_passengers``
    from S into E/I at the matched destination patch.

    Returns float arrays of length ``len(schedule.patch_ids)``. Susceptible
    counts are clipped at zero after withdrawals.
    """
    n_patches = len(schedule.patch_ids)
    init_s = np.full(n_patches, float(default_patch_pop), dtype=np.float64)
    init_e = np.zeros(n_patches, dtype=np.float64)
    init_i = np.zeros(n_patches, dtype=np.float64)
    init_r = np.zeros(n_patches, dtype=np.float64)

    ship_idx = _ship_patch_index(schedule.patch_ids)
    mass = float(ship_outbreak_mass)
    e_ship = _SHIP_FRAC_E * mass
    i_ship = _SHIP_FRAC_I * mass
    init_e[ship_idx] += e_ship
    init_i[ship_idx] += i_ship
    init_s[ship_idx] -= mass
    init_s[ship_idx] = max(init_s[ship_idx], 0.0)

    for flight in network_spec.get("flights", []):
        dest = str(flight["destination"])
        n_passengers = float(flight["n_passengers"])
        pidx = _patch_index_for_flight_destination(schedule.patch_ids, dest)
        if pidx is None:
            logger.warning(
                "Metapop seed: flight destination %r (IATA %r) not in schedule "
                "patch_ids %s; skipping.",
                dest,
                _iata_from_destination(dest),
                schedule.patch_ids,
            )
            continue
        d_e = _FLIGHT_FRAC_E * n_passengers
        d_i = _FLIGHT_FRAC_I * n_passengers
        init_e[pidx] += d_e
        init_i[pidx] += d_i
        withdraw = d_e + d_i
        init_s[pidx] -= withdraw
        init_s[pidx] = max(init_s[pidx], 0.0)

    return init_s, init_e, init_i, init_r
