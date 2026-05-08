import json
from pathlib import Path

from eosp.scripts.build_mobility_bundle import build_bundle


def test_etl_builds_json_with_nseed(tmp_path: Path) -> None:
    routes = tmp_path / "routes.csv"
    routes.write_text(
        "X,1,AMS,1,JNB,0,Y,0,738\n",
        encoding="utf-8",
    )
    out = tmp_path / "out.json"
    n_days = 5
    bundle = build_bundle(
        routes,
        n_days=n_days,
        out_json=out,
        out_parquet=None,
        default_seat_proxy=150.0,
    )

    assert bundle["patch_ids"][0] == "NSEED"
    assert "AMS" in bundle["patch_ids"]
    assert "JNB" in bundle["patch_ids"]
    assert len(bundle["days"]) == n_days

    data = json.loads(out.read_text(encoding="utf-8"))
    assert data == bundle
    meta = json.loads(out.with_suffix(".meta.json").read_text(encoding="utf-8"))
    assert meta["mobility_source"] == "openflights_routes_derived"
    assert meta["horizon_days"] == n_days
