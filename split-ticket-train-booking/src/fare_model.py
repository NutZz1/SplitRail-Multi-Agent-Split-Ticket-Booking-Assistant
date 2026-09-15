"""
Fare model: SYNTHETIC per-class rates applied to REAL distances.

No free dataset of Indian Railways fares exists (the same situation as
seat availability), so the per-km rates below are illustrative values in
a realistic 2024-ish ballpark for long-distance Mail/Express fares. They
are NOT real tariff data and should not be presented as such. Distances,
by contrast, are real: the haversine sum along the train's scheduled stops
(see :mod:`src.distance`).

The model is deliberately linear (fare = km x rate). Real IRCTC fares are
telescopic (per-km rate falls with distance) and carry reservation and
superfast surcharges; none of that is modelled.
"""

from __future__ import annotations

from src.data_store import RailDataStore
from src.distance import route_distance_km, segment_distance_km
from src.state import Ticket

#: SYNTHETIC fare per kilometre by travel class (rupees/km). Illustrative only.
FARE_PER_KM: dict[str, float] = {
    "SL": 0.50,   # Sleeper
    "3A": 1.30,   # AC 3-tier
    "2A": 1.90,   # AC 2-tier
    "1A": 3.20,   # AC First
    "CC": 1.10,   # AC Chair Car
    "EC": 2.20,   # Executive Chair Car
    "2S": 0.35,   # Second Seating
}


def ticket_distance_km(ticket: Ticket, store: RailDataStore) -> float | None:
    """Real distance covered by ``ticket``: along its train's route, else straight-line, else None."""
    km = route_distance_km(ticket.train_number, ticket.from_station, ticket.to_station, store)
    if km is None:
        km = segment_distance_km(ticket.from_station, ticket.to_station, store)
    return km


def compute_fare(ticket: Ticket, store: RailDataStore) -> float | None:
    """Fare for one ticket, or None if its class is unknown or its distance is unavailable."""
    if ticket.travel_class not in FARE_PER_KM:
        return None
    km = ticket_distance_km(ticket, store)
    if km is None:
        return None
    return round(km * FARE_PER_KM[ticket.travel_class], 2)


def compute_total_fare(tickets: tuple[Ticket, ...], store: RailDataStore) -> float | None:
    """Sum of per-ticket fares. None if ANY ticket's fare is unavailable -- never a partial total."""
    if not tickets:
        return None
    total = 0.0
    for t in tickets:
        fare = compute_fare(t, store)
        if fare is None:
            return None
        total += fare
    return round(total, 2)
