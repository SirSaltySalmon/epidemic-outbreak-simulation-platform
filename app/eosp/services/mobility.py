from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class MobilitySchedule:
    patch_ids: tuple[str, ...]
    n_days: int
    day_flows: tuple[dict[tuple[int, int], float], ...]
    total_outflow_per_patch_day: np.ndarray

    def patch_index(self, code: str) -> int:
        return self.patch_ids.index(code)


def load_mobility_schedule(path: Path) -> MobilitySchedule:
    raw = json.loads(path.read_text(encoding="utf-8"))
    patch_ids = tuple(raw["patch_ids"])
    n_patches = len(patch_ids)
    idx = {p: i for i, p in enumerate(patch_ids)}
    days_raw = raw["days"]
    day_flows: list[dict[tuple[int, int], float]] = []

    total_out = np.zeros((len(days_raw), n_patches), dtype=np.float64)
    for d, day in enumerate(days_raw):
        flows: dict[tuple[int, int], float] = {}
        for edge in day.get("flows", []):
            i, j = idx[edge["from"]], idx[edge["to"]]
            n = float(edge["n"])
            flows[(i, j)] = flows.get((i, j), 0.0) + n
            total_out[d, i] += n
        day_flows.append(flows)

    return MobilitySchedule(
        patch_ids=patch_ids,
        n_days=len(day_flows),
        day_flows=tuple(day_flows),
        total_outflow_per_patch_day=total_out,
    )
