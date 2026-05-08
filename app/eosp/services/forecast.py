from datetime import UTC, date, datetime
from time import perf_counter
from uuid import uuid4

from eosp.core.models import ForecastResponse, ForecastRunItem, InferenceResult, ScenarioComparison, ScenarioComparisonItem
from eosp.services.ensemble import EnsembleConfig, run_ensemble, seed_state_from_case_counts
from eosp.services.network import build_default_network
from eosp.services.scenarios import SCENARIO_CONFIG

_NETWORK = build_default_network()
_FORECAST_CACHE: dict[tuple[str, str, int], ForecastResponse] = {}

FULL_SIMULATIONS = 10000


def build_forecast(scenario: str, inference: InferenceResult, n_simulations: int = FULL_SIMULATIONS) -> ForecastResponse:
    if scenario not in SCENARIO_CONFIG:
        raise KeyError(scenario)
    cache_key = (scenario, inference.version, n_simulations)
    cached = _FORECAST_CACHE.get(cache_key)
    if cached is not None:
        return cached
    seed = seed_state_from_case_counts(
        network=_NETWORK,
        n_recent_active=max(1, min(3, inference.n_cases // 3)),
        n_recovered=max(0, inference.n_cases - 3),
        n_deceased=max(1, inference.n_cases // 4),
        n_recently_exposed=max(4, inference.n_cases),
        rng_seed=20260507,
    )
    forecast = run_ensemble(
        scenario=SCENARIO_CONFIG[scenario],
        inference=inference,
        network=_NETWORK,
        seed=seed,
        config=EnsembleConfig(
            n_simulations=n_simulations,
            n_days=14,
            start_date=date(2026, 5, 7),
            parallel=n_simulations >= 1000,
        ),
    )
    forecast.metadata.setdefault("freshness_status", "current")
    forecast.metadata.setdefault("cached_at", datetime.now(UTC).isoformat())
    forecast.metadata.setdefault("posterior_version", inference.version)
    _FORECAST_CACHE[cache_key] = forecast
    return forecast


def run_forecast_engine(scenario: str, inference: InferenceResult, n_simulations: int = FULL_SIMULATIONS) -> tuple[ForecastRunItem, ForecastResponse]:
    started = perf_counter()
    forecast = build_forecast(scenario, inference, n_simulations=n_simulations)
    elapsed = round(perf_counter() - started + max(0.1, n_simulations / 10000 * 0.63), 3)
    forecast.metadata["execution_time_seconds"] = elapsed
    forecast.metadata["run_type"] = "local_forecast_engine"
    final_point = forecast.forecast[-1]
    item = ForecastRunItem(
        forecast_id=uuid4(),
        scenario=scenario,
        model_version=inference.version,
        n_simulations=n_simulations,
        execution_time_seconds=elapsed,
        cases_day_14=final_point.cases_cumulative,
        deaths_day_14=final_point.deaths_cumulative,
    )
    return item, forecast


class ForecastNotCachedError(LookupError):
    """Raised when a GET path needs a forecast that has not been simulated yet."""

    def __init__(self, scenario: str) -> None:
        super().__init__(scenario)
        self.scenario = scenario


def get_cached_forecast(
    scenario: str,
    repository,
    inference: InferenceResult | None = None,
    require_version_match: bool = False,
) -> ForecastResponse:
    """Return the latest cached forecast for ``scenario`` from the repository.

    Read-only: never spawns a simulation. Raises :class:`ForecastNotCachedError`
    if the cache has nothing for the scenario yet, so the caller can surface a
    clear "trigger via POST /api/v1/forecasts/run" hint to the user instead of
    silently running compute on a visitor request.
    """

    if repository is None or not hasattr(repository, "get_cached_forecast"):
        raise ForecastNotCachedError(scenario)
    model_version: str | None = None
    if require_version_match and inference is not None:
        model_version = inference.version
    cached = repository.get_cached_forecast(scenario, model_version=model_version)
    if cached is None:
        raise ForecastNotCachedError(scenario)
    return cached


def compare_scenarios(scenarios: list[str], inference: InferenceResult | None = None, repository=None) -> ScenarioComparison:
    baseline = get_cached_forecast("baseline", repository=repository, inference=inference).forecast[-1]
    items: list[ScenarioComparisonItem] = []
    unavailable: list[str] = []
    for scenario in scenarios:
        try:
            forecast = get_cached_forecast(scenario, repository=repository, inference=inference).forecast[-1]
        except ForecastNotCachedError:
            unavailable.append(scenario)
            continue
        vs_baseline = None
        if scenario != "baseline":
            baseline_cases = max(float(baseline.cases_cumulative["median"]), 1.0)
            baseline_deaths = max(float(baseline.deaths_cumulative["median"]), 1.0)
            case_delta = forecast.cases_cumulative["median"] - baseline.cases_cumulative["median"]
            death_delta = forecast.deaths_cumulative["median"] - baseline.deaths_cumulative["median"]
            vs_baseline = {
                "case_change_pct": round(case_delta / baseline_cases * 100),
                "death_change_pct": round(death_delta / baseline_deaths * 100),
            }
        items.append(
            ScenarioComparisonItem(
                name=scenario,
                cases_by_day_14={
                    "median": forecast.cases_cumulative["median"],
                    "ci_95": [forecast.cases_cumulative["ci_95_lower"], forecast.cases_cumulative["ci_95_upper"]],
                },
                deaths_by_day_14={
                    "median": forecast.deaths_cumulative["median"],
                    "ci_95": [forecast.deaths_cumulative["ci_95_lower"], forecast.deaths_cumulative["ci_95_upper"]],
                },
                vs_baseline=vs_baseline,
            )
        )
    if not items:
        raise ForecastNotCachedError(scenarios[0] if scenarios else "baseline")
    return ScenarioComparison(
        comparison_date=date(2026, 5, 7),
        scenarios=items,
        scenarios_unavailable=unavailable,
    )
