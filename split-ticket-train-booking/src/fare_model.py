"""
Fare model: SYNTHETIC rates and charges applied to REAL distances.

No free dataset of Indian Railways fares exists (the same situation as seat
availability), so every number below is an illustrative value in a realistic
2024-ish ballpark for long-distance Mail/Express travel. They are NOT real
tariff data and must not be presented as such. Distances, by contrast, are
real: the haversine sum along the train's scheduled stops (:mod:`src.distance`).

Why this is not fare = km x rate
--------------------------------
It used to be, and that made the fare dimension useless: distance along a
route is additive, so a ticket A->C cost *exactly* what A->B plus B->C cost.
Fare could never distinguish a direct ride from a split of it, and the
coordinator's ``W_FARE`` term contributed nothing to that comparison.

Real fares are not linear in distance. Two effects matter here, and they
point the same way:

**Telescopic base fare.** The marginal rate per kilometre FALLS as the
journey lengthens (:data:`FARE_BANDS`). A long ticket earns the cheaper
outer bands; two short tickets each restart at the expensive inner ones.

**Per-ticket charges.** Reservation and superfast charges are levied once
per *ticket*, not per kilometre, so splitting pays them twice.

Together they mean **a split ticket costs more than the direct fare it
replaces** -- which is the honest answer for Indian Railways. Split-ticketing
here buys a *confirmed berth*, not a cheaper fare, and that is exactly the
trade-off the coordinator now weighs: a few hundred rupees more against a
seat you will actually get. (The famous UK-style split *savings* come from
fare anomalies between operators and peak boundaries, which a telescopic
distance tariff does not have.)

What is still not modelled: quota and Tatkal pricing, dynamic/flexi fares on
premium trains, catering bundled into Rajdhani/Shatabdi fares, child and
concession fares, and the rounding rules of the real tariff.
"""

from __future__ import annotations

from src.data_store import RailDataStore
from src.distance import route_distance_km, segment_distance_km
from src.state import Ticket

#: Telescopic base rate, SYNTHETIC. ``(upper_km, rupees per km within this
#: band)``; ``None`` is "everything beyond". The rate falls with distance, so
#: the 900th kilometre of a journey costs less than the 90th. Sleeper class is
#: the reference; other classes scale off it via :data:`CLASS_FACTOR`.
FARE_BANDS: tuple[tuple[int | None, float], ...] = (
    (100, 0.72),
    (300, 0.56),
    (700, 0.45),
    (1500, 0.38),
    (None, 0.32),
)

#: Multiplier on the sleeper base fare, by class. SYNTHETIC but ordered and
#: spaced the way real classes are.
CLASS_FACTOR: dict[str, float] = {
    "2S": 0.55,   # Second Seating
    "SL": 1.00,   # Sleeper (the reference class)
    "CC": 2.30,   # AC Chair Car
    "3A": 2.60,   # AC 3-tier
    "2A": 3.70,   # AC 2-tier
    "EC": 4.40,   # Executive Chair Car
    "1A": 6.30,   # AC First
}

#: Flat charge per TICKET, not per kilometre -- this is what makes a split
#: cost more than the journey it replaces. SYNTHETIC, in rupees.
RESERVATION_CHARGE: dict[str, float] = {
    "2S": 15, "SL": 20, "CC": 40, "3A": 40, "2A": 50, "EC": 50, "1A": 60,
}

#: Superfast surcharge, also per ticket. SYNTHETIC, in rupees.
SUPERFAST_CHARGE: dict[str, float] = {
    "2S": 15, "SL": 30, "CC": 45, "3A": 45, "2A": 45, "EC": 45, "1A": 75,
}

#: GST applies to air-conditioned classes only, as it does in reality.
GST_CLASSES = frozenset({"CC", "3A", "2A", "EC", "1A"})
GST_RATE: float = 0.05

#: Every class this model can price. Used for input validation.
FARE_CLASSES: tuple[str, ...] = tuple(CLASS_FACTOR)


def base_fare(km: float) -> float:
    """Telescopic sleeper-class base fare for ``km``, in rupees.

    Each band contributes only the kilometres that fall inside it, so the
    result is continuous and its slope decreases -- which is what makes
    ``base_fare(a + b) < base_fare(a) + base_fare(b)`` for positive a, b.
    """
    total, covered = 0.0, 0.0
    for upper, rate in FARE_BANDS:
        if upper is None:
            total += (km - covered) * rate
            break
        if km <= upper:
            total += (km - covered) * rate
            break
        total += (upper - covered) * rate
        covered = upper
    return total


def ticket_distance_km(ticket: Ticket, store: RailDataStore) -> float | None:
    """Real distance covered by ``ticket``: along its train's route, else straight-line, else None."""
    km = route_distance_km(ticket.train_number, ticket.from_station, ticket.to_station, store)
    if km is None:
        km = segment_distance_km(ticket.from_station, ticket.to_station, store)
    return km


def fare_breakdown(ticket: Ticket, store: RailDataStore, passengers: int = 1) -> dict | None:
    """Every component of one ticket's fare, or None if it cannot be priced.

    Returned separately from :func:`compute_fare` so an interface can show why
    a split costs what it costs -- the doubled flat charges are the
    interesting part, and a single total hides them.
    """
    cls = ticket.travel_class
    if cls not in CLASS_FACTOR or passengers < 1:
        return None
    km = ticket_distance_km(ticket, store)
    if km is None:
        return None

    base = base_fare(km) * CLASS_FACTOR[cls]
    reservation = RESERVATION_CHARGE[cls]
    superfast = SUPERFAST_CHARGE[cls]
    gst = (base + reservation + superfast) * GST_RATE if cls in GST_CLASSES else 0.0
    per_passenger = base + reservation + superfast + gst
    return {
        "distance_km": round(km, 1),
        "base": round(base, 2),
        "reservation_charge": reservation,
        "superfast_charge": superfast,
        "gst": round(gst, 2),
        "per_passenger": round(per_passenger, 2),
        "passengers": passengers,
        "total": round(per_passenger * passengers, 2),
    }


def compute_fare(ticket: Ticket, store: RailDataStore, passengers: int = 1) -> float | None:
    """Fare for one ticket, or None if its class is unknown or its distance is unavailable."""
    parts = fare_breakdown(ticket, store, passengers)
    return None if parts is None else parts["total"]


def compute_total_fare(
    tickets: tuple[Ticket, ...], store: RailDataStore, passengers: int = 1
) -> float | None:
    """Sum of per-ticket fares. None if ANY ticket's fare is unavailable -- never a partial total."""
    if not tickets:
        return None
    total = 0.0
    for t in tickets:
        fare = compute_fare(t, store, passengers)
        if fare is None:
            return None
        total += fare
    return round(total, 2)
