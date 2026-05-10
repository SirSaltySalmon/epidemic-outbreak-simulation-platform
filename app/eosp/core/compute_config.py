"""Minimal dataclass configuration for inference and ensemble workers.

Keeps bootstrap and routing importable without NumPy/JAX/network-science stacks
(slim cloud deployments omit ``[simulation]`` extras).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass
class EnsembleConfig:
    n_simulations: int = 10000
    n_days: int = 14
    start_date: date = date(2026, 5, 7)
    rng_seed: int = 20260507
    parallel: bool = True
    max_workers: int | None = None


@dataclass
class InferenceConfig:
    num_warmup: int = 1000
    num_samples: int = 2000
    num_chains: int = 4
    target_accept_prob: float = 0.85
    rng_seed: int = 20260507
    persist_netcdf: bool = True
    posteriors_dir: Path | None = None
    #: When set, :func:`run_inference` uses this instead of ``network.degree_summary()``
    #: so callers can skip building a :class:`~eosp.services.network.ContactNetwork`.
    network_summary: dict[str, float] | None = None
