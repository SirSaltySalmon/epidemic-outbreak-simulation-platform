from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from eosp.core.models import ForecastResponse, ForecastRunItem, InferenceResult, ScenarioComparison, ScenarioComparisonItem

if TYPE_CHECKING:
    from eosp.core.models import CaseRecord

_FORECAST_CACHE: dict[tuple[str, str, int, str, int], ForecastResponse] = {}

FULL_SIMULATIONS = 10000


def build_forecast(
    scenario: str,
    inference: InferenceResult,
    n_simulations: int = FULL_SIMULATIONS,
    *,
    cases: list[CaseRecord] | None = None,
    index_case_id: str | None = None,
    rng_seed: int | None = None,
) -> ForecastResponse:
    from eosp.core.compute_config import EnsembleConfig
    from eosp.services.hub_timeline.ensemble import run_hub_timeline_forecast

    if cases is None:
        raise ValueError("cases are required to build a hub timeline forecast")

    ec = EnsembleConfig()
    seed = int(rng_seed if rng_seed is not None else ec.rng_seed)
    cache_key = (scenario, inference.version, int(n_simulations), index_case_id or "", seed)
    if cache_key in _FORECAST_CACHE:
        return _FORECAST_CACHE[cache_key]

    response = run_hub_timeline_forecast(
        scenario_name=scenario,
        cases=cases,
        inference=inference,
        n_simulations=int(n_simulations),
        rng_seed=seed,
        index_case_id=index_case_id,
    )
    _FORECAST_CACHE[cache_key] = response
    return response


def run_forecast_engine(
    scenario: str,
    inference: InferenceResult,
    n_simulations: int = FULL_SIMULATIONS,
    *,
    cases: list[CaseRecord] | None = None,
    index_case_id: str | None = None,
    rng_seed: int | None = None,
) -> tuple[ForecastRunItem, ForecastResponse]:
    from eosp.services.hub_timeline.ensemble import run_forecast_simulation

    if cases is None:
        raise ValueError("cases are required to run the hub timeline forecast engine")

    return run_forecast_simulation(
        scenario=scenario,
        inference=inference,
        cases=cases,
        n_simulations=n_simulations,
        index_case_id=index_case_id,
        rng_seed=rng_seed,
    )


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
