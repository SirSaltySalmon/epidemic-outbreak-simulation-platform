from pathlib import Path

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
