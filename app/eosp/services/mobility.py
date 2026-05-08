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


def load_mobility_sidecar_meta(mobility_path: Path) -> dict | None:
    """If ``{stem}.meta.json`` sits next to the mobility file, return it as a dict.

    For ``foo.parquet`` or ``foo.json``, the sidecar file name is ``foo.meta.json``.
    """
    meta_path = mobility_path.with_name(mobility_path.stem + ".meta.json")
    if not meta_path.is_file():
        return None
    raw = json.loads(meta_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return None
    return raw


def load_mobility_schedule(path: Path) -> MobilitySchedule:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return _load_mobility_json(path)
    if suffix == ".parquet":
        return _load_mobility_parquet(path)
    raise ValueError(f"unsupported mobility schedule type: {path.suffix!r}")


def _load_mobility_json(path: Path) -> MobilitySchedule:
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


def _load_mobility_parquet(path: Path) -> MobilitySchedule:
    try:
        import pyarrow.parquet as pq
    except ImportError as e:
        raise ImportError(
            "loading .parquet mobility schedules requires pyarrow; "
            "install with: pip install 'eosp[mobility]'"
        ) from e

    table = pq.read_table(path)
    required = ("day", "origin_iata", "dest_iata", "n")
    for col in required:
        if col not in table.column_names:
            raise ValueError(f"parquet mobility missing required column: {col!r}")

    meta = table.schema.metadata or {}
    key = b"eosp.patch_ids"
    if key not in meta:
        raise ValueError("parquet mobility requires schema metadata key 'eosp.patch_ids'")
    raw_ids = meta[key].decode("utf-8")
    patch_ids = tuple(s.strip() for s in raw_ids.split(",") if s.strip())
    if not patch_ids or patch_ids[0].upper() != "NSEED":
        raise ValueError("eosp.patch_ids must be non-empty and start with NSEED")

    n_patches = len(patch_ids)
    idx = {p.upper(): i for i, p in enumerate(patch_ids)}

    days_arr = table.column("day").combine_chunks().to_numpy(zero_copy_only=False)
    if days_arr.size == 0:
        return MobilitySchedule(
            patch_ids=patch_ids,
            n_days=0,
            day_flows=tuple(),
            total_outflow_per_patch_day=np.zeros((0, n_patches), dtype=np.float64),
        )

    if days_arr.min() < 0:
        raise ValueError("mobility parquet column 'day' must be non-negative integers")
    n_days = int(days_arr.max()) + 1

    day_flows: list[dict[tuple[int, int], float]] = [{} for _ in range(n_days)]
    total_out = np.zeros((n_days, n_patches), dtype=np.float64)

    origins = table.column("origin_iata").combine_chunks().to_pylist()
    dests = table.column("dest_iata").combine_chunks().to_pylist()
    ns = table.column("n").combine_chunks().to_numpy(zero_copy_only=False)

    for d, o, dest, n in zip(days_arr, origins, dests, ns, strict=True):
        di = int(d)
        if di < 0 or di >= n_days:
            raise ValueError(f"mobility row day {di} out of range 0..{n_days - 1}")
        o_code = str(o).strip()
        d_code = str(dest).strip()
        i = idx.get(o_code.upper())
        j = idx.get(d_code.upper())
        if i is None or j is None:
            raise ValueError(f"unknown IATA for patch_ids {patch_ids!r}: {o_code!r} -> {d_code!r}")
        nf = float(n)
        key_ij = (i, j)
        flows = day_flows[di]
        flows[key_ij] = flows.get(key_ij, 0.0) + nf
        total_out[di, i] += nf

    return MobilitySchedule(
        patch_ids=patch_ids,
        n_days=n_days,
        day_flows=tuple(day_flows),
        total_outflow_per_patch_day=total_out,
    )
