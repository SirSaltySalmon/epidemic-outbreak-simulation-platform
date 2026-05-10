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
    n_simulations: int = 100
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
    #: Optional legacy ship/itinerary summary. Hub-surrogate v2 inference ignores
    #: ``mean_weighted_degree`` and related keys; kept so old env-based configs
    #: do not crash.
    network_summary: dict[str, float] | None = None
    inference_model: str = "hub_surrogate_v2"
    p_transmit_prior_alpha: float = 1.0
    p_transmit_prior_beta: float = 80.0
    h2h_log_sigma: float = 0.25
    cfr_prior_alpha: float = 2.0
    cfr_prior_beta: float = 50.0
    background_onset_mean: float = 0.35
    background_onset_concentration: float = 2.0
    reporting_fraction_alpha: float = 8.0
    reporting_fraction_beta: float = 2.0
