from datetime import date
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from eosp.api.deps import require_clerk_session
from eosp.core import read_cache as read_cache_module
from eosp.core.models import CaseCreate, CaseStatus, LabResult
from eosp.core.read_cache import ReadCache
from eosp.core.repository import InMemoryRepository, SqlRepository
from eosp.core.seed_data import ALERTS, CASES, INFERENCES, VALIDATIONS
from eosp.core.tables import Base
from eosp.main import app


class _FakePipeline:
    def __init__(self, redis: "_FakeRedis") -> None:
        self._redis = redis
        self._ops: list = []

    def get(self, key):
        self._ops.append(lambda: self._redis.get(key))

    def hget(self, key, field):
        self._ops.append(lambda: self._redis.hget(key, field))

    def hset(self, key, field, value):
        self._ops.append(lambda: self._redis.hset(key, field, value))

    def expire(self, key, seconds):
        self._ops.append(lambda: 1)

    def exec(self):
        self._redis.round_trips += 1
        if self._redis.fail:
            raise ConnectionError("redis down")
        return [op() for op in self._ops]


class _FakeRedis:
    """Minimal stand-in for ``upstash_redis.Redis`` (string values, one hash)."""

    def __init__(self) -> None:
        self.strings: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.round_trips = 0
        self.fail = False

    def pipeline(self):
        return _FakePipeline(self)

    def get(self, key):
        return self.strings.get(key)

    def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[field] = value
        return 1

    def incr(self, key):
        self.round_trips += 1
        if self.fail:
            raise ConnectionError("redis down")
        self.strings[key] = str(int(self.strings.get(key, "0")) + 1)
        return int(self.strings[key])


def _cache(redis: _FakeRedis, ttl: int = 300) -> ReadCache:
    return ReadCache(redis, namespace="test", ttl_seconds=ttl)


def test_miss_then_store_then_hit_in_one_round_trip():
    redis = _FakeRedis()
    cache = _cache(redis)

    body, token = cache.lookup("k")
    assert body is None and token == "0"
    cache.store("k", '{"a":"x|y"}', token)

    before = redis.round_trips
    body, token = cache.lookup("k")
    assert body == '{"a":"x|y"}'
    assert redis.round_trips == before + 1


def test_invalidate_stops_serving_existing_entries():
    redis = _FakeRedis()
    cache = _cache(redis)
    _, token = cache.lookup("k")
    cache.store("k", "1", token)

    cache.invalidate()

    assert cache.lookup("k") == (None, "1")


def test_store_from_a_read_that_predates_a_write_is_never_served():
    redis = _FakeRedis()
    cache = _cache(redis)
    _, stale_token = cache.lookup("k")  # reader starts
    cache.invalidate()  # a write lands while the reader is still building
    cache.store("k", "stale", stale_token)

    body, _ = cache.lookup("k")
    assert body is None


def test_entries_expire_after_ttl(monkeypatch):
    redis = _FakeRedis()
    cache = _cache(redis, ttl=60)
    now = [1_000_000.0]
    monkeypatch.setattr(read_cache_module.time, "time", lambda: now[0])
    _, token = cache.lookup("k")
    cache.store("k", "1", token)

    now[0] += 59
    assert cache.lookup("k")[0] == "1"
    now[0] += 2
    assert cache.lookup("k")[0] is None


def test_redis_failure_degrades_to_no_cache_and_backs_off():
    redis = _FakeRedis()
    cache = _cache(redis)
    redis.fail = True

    assert cache.lookup("k") == (None, None)
    trips = redis.round_trips
    assert cache.lookup("k") == (None, None)  # cooling down: no further Redis calls
    cache.store("k", "1", None)
    assert redis.round_trips == trips


def test_oversized_body_is_not_stored():
    redis = _FakeRedis()
    cache = _cache(redis)
    _, token = cache.lookup("k")
    cache.store("k", "x" * (read_cache_module._MAX_BODY_BYTES + 1), token)
    assert cache.lookup("k")[0] is None


@pytest.fixture
def cached_client():
    redis = _FakeRedis()
    repo = InMemoryRepository(
        cases=list(CASES),
        validations=dict(VALIDATIONS),
        inferences=list(INFERENCES),
        alerts=list(ALERTS),
    )
    repo.read_cache = _cache(redis)
    previous = app.state.repository
    app.state.repository = repo
    app.dependency_overrides[require_clerk_session] = lambda: None
    try:
        yield TestClient(app), repo
    finally:
        app.state.repository = previous
        app.dependency_overrides.pop(require_clerk_session, None)


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/dashboard/bootstrap",
        "/api/v1/geo/outbreak",
        "/api/v1/geo/bundle",
        "/api/v1/cases/summary",
        "/api/v1/cases",
        "/api/v1/inference/versions",
    ],
)
def test_route_served_from_cache_until_invalidated(cached_client, path):
    client, repo = cached_client

    first = client.get(path)
    assert first.status_code == 200
    assert first.headers["x-eosp-cache"] == "miss"
    assert first.headers["cache-control"].startswith("public")

    # Change the data behind the cache's back: a hit must still return the first body.
    repo.cases.clear()
    repo.inferences.clear()
    second = client.get(path)
    assert second.headers["x-eosp-cache"] == "hit"
    assert second.content == first.content
    assert second.headers["cache-control"] == first.headers["cache-control"]
    assert second.headers["content-type"] == first.headers["content-type"]

    repo.read_cache.invalidate()
    third = client.get(path)
    assert third.headers["x-eosp-cache"] == "miss"
    assert third.content != first.content


def test_unbounded_query_values_are_not_cached(cached_client):
    client, repo = cached_client

    filtered = client.get("/api/v1/cases?country=ZA")
    assert filtered.status_code == 200
    assert "x-eosp-cache" not in filtered.headers

    unknown = client.get("/api/v1/dashboard/bootstrap?scenarios=baseline,not_a_scenario")
    assert unknown.status_code == 200
    assert "x-eosp-cache" not in unknown.headers

    assert repo.read_cache._redis.hashes == {}


def test_error_responses_are_not_cached(cached_client):
    client, repo = cached_client

    response = client.get("/api/v1/forecasts/baseline")
    assert response.status_code == 503
    assert repo.read_cache._redis.hashes == {}


def test_routes_without_cache_send_no_cache_header(cached_client):
    client, repo = cached_client
    repo.read_cache = None

    response = client.get("/api/v1/cases/summary")
    assert response.status_code == 200
    assert "x-eosp-cache" not in response.headers


def _sqlite_repository(cache: ReadCache) -> SqlRepository:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return SqlRepository(
        session_factory=sessionmaker(bind=engine, expire_on_commit=False),
        read_cache=cache,
    )


def _new_case() -> CaseCreate:
    return CaseCreate(
        case_id=uuid4(),
        patient_identifier="cache-test-patient",
        symptom_onset_date=date(2026, 4, 28),
        location_country="ZA",
        location_airport_code="JNB",
        confirmed_or_suspected=CaseStatus.CONFIRMED,
        lab_test_result=LabResult.PCR_POSITIVE,
        contacts=[],
        data_source="test",
        updated_by="pytest",
        updated_reason="New_case",
    )


def test_sql_repository_writes_invalidate_the_cache():
    redis = _FakeRedis()
    cache = _cache(redis)
    repo = _sqlite_repository(cache)

    _, token = cache.lookup("cases|")
    cache.store("cases|", "[]", token)
    assert cache.lookup("cases|")[0] == "[]"

    case, _validation = repo.create_case(_new_case())
    assert cache.lookup("cases|")[0] is None

    _, token = cache.lookup("cases|")
    cache.store("cases|", "[1]", token)
    assert repo.delete_case(case.case_id) is True
    assert cache.lookup("cases|")[0] is None


def test_sql_repository_list_inferences_is_bounded():
    repo = _sqlite_repository(_cache(_FakeRedis()))
    for index, inference in enumerate(INFERENCES[:1] * 3):
        repo.update_inference(inference.model_copy(update={"version": f"v-test-{index}"}))

    assert len(repo.list_inferences(limit=2)) == 2
    assert len(repo.inferences) == 3
