"""Itinerary legs for itinerary ABM (design §7).

Movement derives from the flight baseline / ledger attached to
:class:`~eosp.services.network.ContactNetwork` (Track B), not from a static
network_spec JSON.
"""

from __future__ import annotations

from eosp.services.network import ContactNetwork

# Formerly imported from abm — keep in sync if a new simulator reintroduces state codes.
STATE_H = 6
STATE_D = 4


def entity_may_enqueue_flight_legs(state_code: int) -> bool:
    """Hospitalised and deceased agents do not board further commercial legs (§7.2)."""

    return state_code not in (STATE_H, STATE_D)


def build_itinerary(entity_index: int, network: ContactNetwork) -> list[tuple[int, str, str]]:
    """Return ``(departure_day, origin_iata, destination_iata)`` legs from the built world."""

    book = network.agent_itinerary_legs or {}
    return list(book.get(int(entity_index), []))


def build_itinerary_p1(entity_index: int, spec: object, network: ContactNetwork) -> list[tuple[int, str, str]]:
    """Deprecated: use :func:`build_itinerary`. ``spec`` is ignored."""

    _ = spec
    return build_itinerary(entity_index, network)


RISK_METRIC_DETAIL = (
    "Infections among hub contact-cohort agents and in-flight co-location. "
    "Broader community transmission at airports is not modelled in this version."
)
