"""Hub timeline epidemic simulator package.

For heavy paths use ``eosp.services.hub_timeline.kernel`` or ``ensemble`` directly.
"""

from eosp.services.hub_timeline.config import HubTimelineSimulatorConfig, default_hub_timeline_config
from eosp.services.hub_timeline.timeline import (
    compute_simulation_calendar,
    eligible_hub_cases,
    eligible_individual_cases,
)

__all__ = [
    "HubTimelineSimulatorConfig",
    "compute_simulation_calendar",
    "default_hub_timeline_config",
    "eligible_hub_cases",
    "eligible_individual_cases",
    "run_forecast_simulation",
    "run_hub_timeline_forecast",
]


def run_hub_timeline_forecast(*args, **kwargs):  # type: ignore[no-untyped-def]
    from eosp.services.hub_timeline.ensemble import run_hub_timeline_forecast as _run

    return _run(*args, **kwargs)


def run_forecast_simulation(*args, **kwargs):  # type: ignore[no-untyped-def]
    from eosp.services.hub_timeline.ensemble import run_forecast_simulation as _run

    return _run(*args, **kwargs)
