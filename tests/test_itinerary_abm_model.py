"""Tests for itinerary ABM design (2026-05-09-itinerary-abm-model-design)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from eosp.core.models import InferenceResult, ParameterEstimate, TriggerType
from eosp.core.tables import Base, FlightLegRow, FlightSnapshotRow
from eosp.services import abm
from eosp.services.abm import SeedState, simulate_trajectory
from eosp.services.ensemble import EnsembleConfig, run_ensemble
from eosp.services.flight_ledger import insert_leg, insert_snapshot_if_absent
from eosp.services.itinerary import RISK_METRIC_DETAIL, build_itinerary, entity_may_enqueue_flight_legs
from eosp.services.network import ContactNetwork, EdgeLayer, assign_flight_passengers_from_spec, build_default_network, gateway_weights_from_network_spec
from eosp.services.scenarios import ScenarioSpec
from eosp.services.world_builder import build_world_contact_network


def _two_agent_dense_ship() -> ContactNetwork:
    return ContactNetwork(
        n_agents=2,
        layers=[EdgeLayer("pair", "household", [(0, 1, 200.0)], 0, 14)],
        node_metadata=[
            {"ship_member": True, "destination": None},
            {"ship_member": True, "destination": None},
        ],
        n_days=14,
    )


def test_P_state_transmits_before_symptoms():
    net = _two_agent_dense_ship()
    rng = np.random.default_rng(0)
    params = {
        "p_transmit": 0.6,
        "h2h_multiplier": 3.0,
        "incubation_mean": 100.0,
        "cfr": 0.0,
        "k_presym": 0.5,
    }
    seed = SeedState(exposed=[], infectious=[], recovered=[], deceased=[], presymptomatic=[0])
    traj_p = simulate_trajectory(net, params, seed=seed, n_days=3, rng=rng)

    rng2 = np.random.default_rng(0)
    seed_e = SeedState(exposed=[0], infectious=[], recovered=[], deceased=[], presymptomatic=None)
    traj_e = simulate_trajectory(net, params, seed=seed_e, n_days=1, rng=rng2)
    assert traj_p.new_cases_per_day[1:].sum() >= 1
    assert traj_e.new_cases_per_day[1] == 0


def test_H_state_no_outbound_legs():
    assert entity_may_enqueue_flight_legs(abm.STATE_H) is False
    assert entity_may_enqueue_flight_legs(abm.STATE_D) is False
    assert entity_may_enqueue_flight_legs(abm.STATE_I) is True


def test_presym_ratio_sensitivity():
    net = build_default_network(n_days=10)
    base = {"p_transmit": 0.14, "h2h_multiplier": 1.3, "incubation_mean": 7.0, "cfr": 0.2}
    seed = SeedState(exposed=[], infectious=[0, 1, 2], recovered=[], deceased=[])
    low = simulate_trajectory(
        net, {**base, "k_presym": 0.2}, seed=seed, n_days=10, rng=np.random.default_rng(99)
    )
    high = simulate_trajectory(
        net, {**base, "k_presym": 0.8}, seed=seed, n_days=10, rng=np.random.default_rng(99)
    )
    assert int(high.cumulative_cases[-1]) >= int(low.cumulative_cases[-1])


def test_mass_conservation():
    net = build_default_network()
    rng = np.random.default_rng(1)
    seed = SeedState(exposed=[2], infectious=[0], recovered=[], deceased=[])
    traj = simulate_trajectory(net, {"p_transmit": 0.1, "cfr": 0.3}, seed=seed, n_days=12, rng=rng)
    n = net.n_agents
    for day in range(traj.daily_counts.shape[0]):
        assert int(traj.daily_counts[day].sum()) == n


def test_gateway_weights_match_passenger_counts():
    spec = {
        "flights": [
            {"destination": "ZA_JNB", "n_passengers": 60, "name": "a"},
            {"destination": "NL_AMS", "n_passengers": 40, "name": "b"},
        ]
    }
    w = gateway_weights_from_network_spec(spec, allowlist=None, pseudocount=0.0)
    assert w["JNB"] == pytest.approx(0.6, abs=1e-9)
    assert w["AMS"] == pytest.approx(0.4, abs=1e-9)


def test_gateway_empirical_draws_near_weights():
    spec = {
        "flights": [
            {"destination": "ZA_JNB", "n_passengers": 60, "name": "a"},
            {"destination": "NL_AMS", "n_passengers": 40, "name": "b"},
        ]
    }
    w = gateway_weights_from_network_spec(spec, pseudocount=0.0)
    rng = np.random.default_rng(12345)
    draws = rng.choice(["JNB", "AMS"], size=100, p=[w["JNB"], w["AMS"]])
    frac_jnb = np.mean(draws == "JNB")
    assert abs(frac_jnb - 0.6) < 0.12


def test_gateway_pseudocount_preserves_mass_on_zero_weight():
    spec = {"flights": [{"destination": "ZA_JNB", "n_passengers": 40, "name": "a"}]}
    w = gateway_weights_from_network_spec(spec, allowlist=["JNB", "AMS"], pseudocount=0.5)
    assert w["JNB"] > w["AMS"]
    assert w["AMS"] >= 0.01


def test_snapshot_idempotent_insert():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    snap = FlightSnapshotRow(
        snapshot_id="abc123",
        provider_id="openflights_static",
        query_fingerprint="fp1",
        fetched_at_utc=datetime.now(UTC),
        horizon_start_date=date(2026, 1, 1),
        horizon_end_date=date(2026, 1, 7),
        status="complete",
    )
    with factory() as session:
        assert insert_snapshot_if_absent(session, snap) is True
        session.commit()
    dup = FlightSnapshotRow(
        snapshot_id="abc123",
        provider_id="openflights_static",
        query_fingerprint="fp2",
        fetched_at_utc=datetime.now(UTC),
        horizon_start_date=date(2026, 2, 1),
        horizon_end_date=date(2026, 2, 7),
        status="partial",
    )
    with factory() as session:
        assert insert_snapshot_if_absent(session, dup) is False
        session.commit()


def test_leg_amend_inserts_new_row():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    snap = FlightSnapshotRow(
        snapshot_id="snap1",
        provider_id="test",
        query_fingerprint="fp",
        fetched_at_utc=datetime.now(UTC),
        horizon_start_date=date(2026, 1, 1),
        horizon_end_date=date(2026, 1, 2),
        status="complete",
    )
    with factory() as session:
        insert_snapshot_if_absent(session, snap)
        first = FlightLegRow(
            snapshot_id="snap1",
            origin_iata="JNB",
            destination_iata="ZRH",
            scheduled_departure_utc=datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
            scheduled_arrival_utc=datetime(2026, 1, 1, 20, 0, tzinfo=UTC),
            leg_status="active",
        )
        insert_leg(session, first)
        session.commit()
        first_id = first.id
    with factory() as session:
        row = session.scalars(select(FlightLegRow).where(FlightLegRow.id == first_id)).one()
        replacement = FlightLegRow(
            snapshot_id=row.snapshot_id,
            origin_iata=row.origin_iata,
            destination_iata=row.destination_iata,
            scheduled_departure_utc=row.scheduled_departure_utc,
            scheduled_arrival_utc=row.scheduled_arrival_utc,
            leg_status="amended",
            supersedes_leg_id=row.id,
        )
        insert_leg(session, replacement)
        session.commit()
    with factory() as session:
        legs = session.scalars(select(FlightLegRow).order_by(FlightLegRow.id)).all()
        assert len(legs) == 2
        assert legs[0].leg_status == "active"
        assert legs[1].supersedes_leg_id == legs[0].id


def test_rerun_same_snapshot_id_abm_deterministic():
    net = build_default_network(n_days=5)
    params = {"p_transmit": 0.05, "cfr": 0.1}
    seed = SeedState(exposed=[1], infectious=[], recovered=[], deceased=[])
    rng = np.random.default_rng(777)
    a = simulate_trajectory(net, params, seed=seed, n_days=5, rng=rng)
    rng2 = np.random.default_rng(777)
    b = simulate_trajectory(net, params, seed=seed, n_days=5, rng=rng2)
    assert np.array_equal(a.cumulative_cases, b.cumulative_cases)


def test_degraded_flag_surfaces_in_forecast_metadata():
    net = build_default_network(n_days=5)
    inference = InferenceResult(
        version="t",
        timestamp=datetime.now(UTC),
        trigger=TriggerType.MANUAL,
        n_cases=1,
        parameters={
            "p_transmit": ParameterEstimate(mean=0.08, std=0.01, ci_95=(0.06, 0.10)),
            "contacts_daily": ParameterEstimate(mean=2.5, std=0.1, ci_95=(2.3, 2.7)),
            "incubation": ParameterEstimate(mean=8.0, std=0.5, ci_95=(7.0, 9.0)),
            "h2h_multiplier": ParameterEstimate(mean=1.0, std=0.05, ci_95=(0.9, 1.1)),
            "cfr": ParameterEstimate(mean=0.35, std=0.02, ci_95=(0.30, 0.40)),
        },
        diagnostics={},
        posterior_download_url="http://example.invalid/posterior.nc",
    )
    scenario = ScenarioSpec(
        name="degraded_test",
        parameter_overrides={"degraded_flight_coverage": True},
    )
    out = run_ensemble(
        scenario=scenario,
        inference=inference,
        network=net,
        seed=SeedState(exposed=[], infectious=[0], recovered=[], deceased=[]),
        config=EnsembleConfig(n_simulations=20, n_days=4, parallel=False),
    )
    assert out.metadata.get("degraded_flight_coverage") is True
    assert RISK_METRIC_DETAIL in out.metadata.get("risk_metric_detail", "")


def test_build_itinerary_uses_flight_assignment():
    fx = Path(__file__).resolve().parent / "fixtures" / "openflights_mini"
    net = build_world_contact_network(
        n_days=12,
        routes_path=fx / "routes.dat",
        airports_path=fx / "airports.dat",
        cases=[],
    )
    labels = set(net.itinerary_bucket_labels or ())
    found = False
    for entity_index in range(net.n_agents):
        legs = build_itinerary(entity_index, net)
        if not legs:
            continue
        assert legs[0][2] in labels
        found = True
        break
    assert found


def test_flight_baseline_bundle_exists():
    p = Path(__file__).resolve().parents[1] / "app" / "eosp" / "data" / "flight_schedules_baseline.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    assert "bundle_date" in data
