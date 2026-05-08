from pathlib import Path

import pytest

from eosp.services.mobility import load_mobility_schedule


def test_load_mobility_schedule_builds_patch_index():
    base = Path(__file__).resolve().parents[1] / "app" / "eosp" / "data"
    sched = load_mobility_schedule(base / "mobility_weekly_skeleton.json")
    assert sched.patch_ids == ("NSEED", "HUB1", "HUB2", "LEAF")
    assert sched.n_days >= 3
    day0 = sched.day_flows[0]
    assert day0[(0, 1)] >= 100
    assert sched.total_outflow_per_patch_day.shape == (
        sched.n_days,
        len(sched.patch_ids),
    )


def test_load_mobility_parquet_roundtrip(tmp_path):
    pytest.importorskip("pyarrow")
    import pyarrow as pa
    import pyarrow.parquet as pq

    schema = pa.schema(
        [
            pa.field("day", pa.int32()),
            pa.field("origin_iata", pa.string()),
            pa.field("dest_iata", pa.string()),
            pa.field("n", pa.float64()),
        ],
        metadata={b"eosp.patch_ids": b"NSEED,AMS,JNB"},
    )
    table = pa.table(
        {
            "day": [0, 0],
            "origin_iata": ["NSEED", "NSEED"],
            "dest_iata": ["AMS", "AMS"],
            "n": [3.0, 7.0],
        },
        schema=schema,
    )
    parquet_path = tmp_path / "mobility.parquet"
    pq.write_table(table, parquet_path)

    sched = load_mobility_schedule(parquet_path)
    assert sched.patch_ids == ("NSEED", "AMS", "JNB")
    assert sched.day_flows[0][(0, 1)] == 10.0
    assert sched.total_outflow_per_patch_day[0, 0] == 10.0
