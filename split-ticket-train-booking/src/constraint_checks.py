"""
Constraint checks and small helpers shared by every successor generator.

Both :mod:`src.successors` (same-train) and
:mod:`src.successors_different_train` apply the same hard constraints from
:class:`~src.state.UserQuery` -- class, seat availability, transfer budget,
run date, cycle prevention -- and build tickets the same way. That logic
lives here so the two generators cannot drift apart.

Nothing in this module generates moves; it only answers yes/no questions
about a candidate move and materialises tickets.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import replace

from src.data_store import (  # noqa: F401  (coach_class/COACH_CLASS_PREFIXES re-exported)
    COACH_CLASS_PREFIXES,
    RailDataStore,
    Stop,
    coach_class,
)
from src.state import JourneyState, Ticket, UserQuery

#: Lower is better when picking a coach.
STATUS_PRIORITY = {"CONFIRMED": 0, "RAC": 1, "WAITLIST": 2}


# --------------------------------------------------------------------------- #
# Coach / class / time utilities
# --------------------------------------------------------------------------- #
# ``coach_class`` and ``COACH_CLASS_PREFIXES`` moved to :mod:`src.data_store`
# (the coach-code convention belongs to the source data) and are re-exported
# above, so `from src.constraint_checks import coach_class` still works.


def stop_datetime(stop: Stop, travel_date: dt.date, *, use_arrival: bool) -> dt.datetime | None:
    """Full datetime of a stop's arrival or departure on a run starting ``travel_date``."""
    clock = stop.arrival if use_arrival else stop.departure
    if clock is None:
        return None
    day = stop.journey_day if stop.journey_day is not None else 1
    h, m = (int(x) for x in clock.split(":")[:2])
    return dt.datetime.combine(travel_date, dt.time(h, m)) + dt.timedelta(days=day - 1)


def best_coach_per_class(
    availability: dict[str, str],
    composition: list[str],
    *,
    exclude: str | None = None,
    near: str | None = None,
) -> list[tuple[str, str, str]]:
    """Pick the single best bookable coach of each class from an availability dict.

    "Best" = better status first (CONFIRMED > RAC > WAITLIST), then the coach
    physically closest to ``near`` (when given), then rake order. ``exclude``
    drops one coach (the one currently occupied). Returns
    ``[(coach, class, status), ...]`` in rake order of the chosen coaches.
    """
    position = {code: i for i, code in enumerate(composition)}
    near_pos = position.get(near) if near else None

    def rank(coach: str, status: str) -> tuple:
        pos = position.get(coach, len(composition))
        dist = abs(pos - near_pos) if near_pos is not None else 0
        return (STATUS_PRIORITY.get(status, 99), dist, pos)

    best: dict[str, tuple[str, str, str]] = {}
    for coach, status in availability.items():
        if coach == exclude or not is_bookable(status):
            continue
        cls = coach_class(coach)
        if cls is None:
            continue
        current = best.get(cls)
        if current is None or rank(coach, status) < rank(current[0], current[2]):
            best[cls] = (coach, cls, status)
    return sorted(best.values(), key=lambda t: position.get(t[0], len(composition)))


def route_index(stops: list[Stop], station_code: str) -> int | None:
    """Index of ``station_code`` in an ordered stop list, or None."""
    for i, s in enumerate(stops):
        if s.station_code == station_code:
            return i
    return None


def ticket_origin(state: JourneyState, query: UserQuery) -> str:
    """Station where the in-progress (not yet closed) ticket started."""
    return state.tickets_so_far[-1].to_station if state.tickets_so_far else query.origin_station


# --------------------------------------------------------------------------- #
# Hard-constraint predicates
# --------------------------------------------------------------------------- #
def class_allowed(cls: str | None, query: UserQuery) -> bool:
    """Class constraint: only binding when ``class_is_hard_constraint``."""
    return not query.class_is_hard_constraint or cls == query.travel_class_preference


def is_bookable(status: str | None) -> bool:
    """CONFIRMED / RAC / WAITLIST may be ridden; UNAVAILABLE (or no data) may not."""
    return status is not None and status != "UNAVAILABLE"


def transfer_budget_allows(state: JourneyState, query: UserQuery) -> bool:
    """True if one more transfer stays within ``query.max_transfers``."""
    return state.transfer_count + 1 <= query.max_transfers


def runs_on_travel_date(store: RailDataStore, train_number: str, query: UserQuery) -> bool:
    return store.runs_on_date(train_number, query.travel_date.isoformat())


def not_revisiting(station_code: str, state: JourneyState) -> bool:
    """Cycle prevention: never move to a station already used as a ticket endpoint."""
    return station_code not in state.visited_stations


# --------------------------------------------------------------------------- #
# Seat availability
# --------------------------------------------------------------------------- #
# A ticket A -> B in one coach needs exactly ONE bookable availability pair:
# (A, B). Intermediate pairs (A, X) for X between A and B are irrelevant --
# that is how real reservations work, and it also matters for the synthetic
# data, where every pair is an independent random draw: checking every
# intermediate pair would wrongly kill almost every long ticket.
#
# Because the search decides *where* a ticket closes only later (at a
# transfer or at the destination), riding onward is allowed as long as the
# in-progress ticket can still be closed at SOME later halt, and closing it
# requires that specific pair to be bookable.


def closing_candidates(stops: list[Stop], start_idx: int, destination: str) -> list[str]:
    """Halts at which a ticket could be closed: ``stops[start_idx:]`` up to and
    including ``destination`` if the route reaches it (you never ride past the goal)."""
    codes = [s.station_code for s in stops[start_idx:]]
    if destination in codes:
        codes = codes[: codes.index(destination) + 1]
    return codes


def bookable_coaches(
    store: RailDataStore, train_number: str, origin: str, candidates: list[str],
    run_date: str | None = None,
) -> dict[str, str]:
    """``{coach: best status}`` over every bookable pair (origin -> X), X in ``candidates``.

    A coach appears iff at least one closing point ahead is bookable in it;
    the status reported is the best it achieves at any of those points.

    ``run_date`` makes the answer date-specific: berths already reserved on
    that date are deducted, so a coach booked out on one day disappears from
    the search for that day only. This is the single point through which
    every successor generator -- and therefore BFS, UCS and A* alike -- sees
    bookings.
    """
    by_destination = store.get_availability_from(train_number, origin, run_date)
    best: dict[str, str] = {}
    for x in candidates:
        for coach, status in by_destination.get(x, {}).items():
            if not is_bookable(status):
                continue
            if coach not in best or STATUS_PRIORITY[status] < STATUS_PRIORITY[best[coach]]:
                best[coach] = status
    return best


# --------------------------------------------------------------------------- #
# Ticket materialisation
# --------------------------------------------------------------------------- #
def close_ticket(state: JourneyState, query: UserQuery, store: RailDataStore, stops: list[Stop]) -> Ticket:
    """Materialise the in-progress ticket, ending at ``state.current_station``.

    The ticket runs from the in-progress origin (last closed ticket's
    destination, or the query origin) to the current station, and carries
    the exact availability status for that (train, from, to, coach).
    ``stops`` is the current train's real-halt list.
    """
    train_number = state.current_train
    assert train_number is not None
    origin = ticket_origin(state, query)
    origin_idx = route_index(stops, origin)
    boarded_at = (
        stop_datetime(stops[origin_idx], query.travel_date, use_arrival=False)
        if origin_idx is not None else None
    )
    status = (
        store.get_availability(
            train_number, origin, state.current_station, query.travel_date.isoformat()
        ).get(state.current_coach)
        if state.current_coach is not None else None
    )
    return Ticket(
        train_number=train_number,
        from_station=origin,
        to_station=state.current_station,
        coach=state.current_coach,
        travel_class=state.current_class,
        boarding_datetime=boarded_at if boarded_at is not None else state.arrival_datetime,
        alighting_datetime=state.arrival_datetime,
        status=status,
    )


def close_final_ticket(state: JourneyState, query: UserQuery, store: RailDataStore) -> JourneyState:
    """Return ``state`` with its in-progress ticket closed at the current station.

    Search algorithms call this on the goal state they return, so that
    ``tickets_so_far`` describes the complete itinerary (origin ->
    destination) rather than only the segments closed by transfers. A
    not-yet-boarded state is returned unchanged.
    """
    if not state.is_boarded:
        return state
    stops = store.get_real_halt_stops(state.current_train)  # type: ignore[arg-type]
    ticket = close_ticket(state, query, store, stops)
    return replace(state, tickets_so_far=state.tickets_so_far + (ticket,))
