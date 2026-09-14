"""
Successor generation for the journey search (same-train transfers only).

Three kinds of move are generated from a :class:`~src.state.JourneyState`:

``board``
    From the not-yet-boarded start state: board a train that halts here,
    runs on the travel date, and has a further real halt to go to. One
    successor is generated per *travel class* that has a bookable coach on
    the first segment, using the best coach in that class (see
    :func:`best_coach_per_class`). Choosing the coach at boarding time (rather
    than deferring it) keeps every boarded state fully specified, which is
    what the availability checks and the eventual Ticket need.

``continue``
    From a boarded state: ride the same train, same coach, to its next real
    halt. This is exactly one edge of the station graph in
    :mod:`src.rail_graph`, and the cost added is the same departure->arrival
    moving time that graph uses, so g(n) and the heuristic agree on units.

``transfer``
    From a boarded state at an intermediate real halt: close the in-progress
    ticket here and continue on the same train in a different coach. Only
    allowed when the halt is long enough (``MIN_COACH_SWITCH_MINUTES``), the
    transfer budget allows it, and the new coach has room for the onward
    segment. This is the split-ticket mechanism itself. Transfers are never
    generated at the boarding station: choosing the initial coach is what
    the ``board`` move already does, and a zero-length ticket is meaningless.

Availability semantics
    A ticket covers (ticket origin -> alighting station) in one coach, so
    the check for a ``continue`` move is on the pair
    (origin of the in-progress ticket, next halt) — the segment the ticket
    would cover if it were closed at the next halt — not merely on the
    single hop. ``UNAVAILABLE`` prunes the move; ``RAC``/``WAITLIST`` are
    allowed and surface as ``Ticket.status``/``Ticket.is_risky`` when the
    ticket is closed.

Dates
    ``query.travel_date`` is the date the train departs its *origin*
    (that is what ``train_run_dates`` records). Stop datetimes are
    ``travel_date + (journey_day - 1)`` + the scheduled clock time, so an
    overnight train like 12658 arrives at MAS on the following calendar
    date automatically.

Different-train transfers are deliberately not implemented yet.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import replace

from src.data_store import RailDataStore, Stop
from src.rail_graph import segment_minutes
from src.state import JourneyState, Ticket, UserQuery

logger = logging.getLogger(__name__)

#: Minimum scheduled halt (minutes) for a passenger to change coach on the same train.
MIN_COACH_SWITCH_MINUTES: float = 5.0

#: Lower is better when picking a coach.
STATUS_PRIORITY = {"CONFIRMED": 0, "RAC": 1, "WAITLIST": 2}

#: Coach-code prefix -> travel class. Longer prefixes are matched first.
COACH_CLASS_PREFIXES: tuple[tuple[str, str], ...] = (
    ("BE", "3A"),  # 3-tier AC economy
    ("S", "SL"),
    ("B", "3A"),
    ("A", "2A"),
    ("H", "1A"),
    ("D", "CC"),   # chair-car rakes (e.g. 12609) use D-series coaches
    ("C", "CC"),
    ("E", "EC"),
)


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def coach_class(coach_code: str | None) -> str | None:
    """Map a coach code like ``S3``/``B1``/``H1`` to its class, or None if unreserved/service."""
    if not coach_code:
        return None
    for prefix, cls in COACH_CLASS_PREFIXES:
        if coach_code.startswith(prefix) and coach_code[len(prefix):].isdigit():
            return cls
    return None


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
        if coach == exclude or status == "UNAVAILABLE":
            continue
        cls = coach_class(coach)
        if cls is None:
            continue
        current = best.get(cls)
        if current is None or rank(coach, status) < rank(current[0], current[2]):
            best[cls] = (coach, cls, status)
    return sorted(best.values(), key=lambda t: position.get(t[0], len(composition)))


def _route_index(stops: list[Stop], station_code: str) -> int | None:
    for i, s in enumerate(stops):
        if s.station_code == station_code:
            return i
    return None


def _ticket_origin(state: JourneyState, query: UserQuery) -> str:
    """Station where the in-progress (not yet closed) ticket started."""
    return state.tickets_so_far[-1].to_station if state.tickets_so_far else query.origin_station


def _class_allowed(cls: str | None, query: UserQuery) -> bool:
    return not query.class_is_hard_constraint or cls == query.travel_class_preference


def _close_ticket(state: JourneyState, query: UserQuery, store: RailDataStore, stops: list[Stop]) -> Ticket:
    """Materialise the in-progress ticket, ending at ``state.current_station``.

    The ticket runs from the in-progress origin (last closed ticket's
    destination, or the query origin) to the current station, and carries
    the exact availability status for that (train, from, to, coach).
    """
    train_number = state.current_train
    assert train_number is not None
    origin = _ticket_origin(state, query)
    origin_idx = _route_index(stops, origin)
    boarded_at = (
        stop_datetime(stops[origin_idx], query.travel_date, use_arrival=False)
        if origin_idx is not None else None
    )
    status = (
        store.get_availability(train_number, origin, state.current_station).get(state.current_coach)
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
    ticket = _close_ticket(state, query, store, stops)
    return replace(state, tickets_so_far=state.tickets_so_far + (ticket,))


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def get_successors(state: JourneyState, query: UserQuery, store: RailDataStore) -> list[JourneyState]:
    """All legal next states from ``state`` under ``query``'s hard constraints."""
    if not state.is_boarded:
        return _board_successors(state, query, store)
    return _continue_successors(state, query, store) + _transfer_successors(state, query, store)


# --------------------------------------------------------------------------- #
# Move generators
# --------------------------------------------------------------------------- #
def _board_successors(state: JourneyState, query: UserQuery, store: RailDataStore) -> list[JourneyState]:
    date_str = query.travel_date.isoformat()
    visited = state.visited_stations
    out: list[JourneyState] = []

    for train_number in store.get_all_trains_through_station(state.current_station):
        if not store.runs_on_date(train_number, date_str):
            continue
        stops = store.get_real_halt_stops(train_number)
        idx = _route_index(stops, state.current_station)
        if idx is None or idx == len(stops) - 1:
            continue  # only passes through here, or terminates here: nothing to ride
        here, nxt = stops[idx], stops[idx + 1]
        if nxt.station_code in visited:
            continue
        depart_at = stop_datetime(here, query.travel_date, use_arrival=False)
        if depart_at is None:
            continue

        availability = store.get_availability(train_number, here.station_code, nxt.station_code)
        if not availability:
            logger.debug("board %s at %s: no availability data for %s->%s, skipping",
                         train_number, here.station_code, here.station_code, nxt.station_code)
            continue
        composition = store.get_coach_composition(train_number)
        for coach, cls, _status in best_coach_per_class(availability, composition):
            if not _class_allowed(cls, query):
                continue
            out.append(replace(
                state,
                arrival_datetime=depart_at,
                current_train=train_number,
                current_coach=coach,
                current_class=cls,
            ))
    return out


def _continue_successors(state: JourneyState, query: UserQuery, store: RailDataStore) -> list[JourneyState]:
    train_number = state.current_train
    assert train_number is not None
    if not store.runs_on_date(train_number, query.travel_date.isoformat()):
        return []
    if not _class_allowed(state.current_class, query):
        return []

    stops = store.get_real_halt_stops(train_number)
    idx = _route_index(stops, state.current_station)
    if idx is None or idx == len(stops) - 1:
        return []
    here, nxt = stops[idx], stops[idx + 1]
    if nxt.station_code in state.visited_stations:
        return []

    minutes = segment_minutes(here, nxt)
    if minutes is None or minutes <= 0:
        logger.warning("train %s: %s->%s has unusable timing (%s), pruning",
                       train_number, here.station_code, nxt.station_code, minutes)
        return []
    arrive_at = stop_datetime(nxt, query.travel_date, use_arrival=True)
    if arrive_at is None:
        return []

    # The in-progress ticket must have room across everything it would cover.
    if state.current_coach is not None:
        origin = _ticket_origin(state, query)
        status = store.get_availability(train_number, origin, nxt.station_code).get(state.current_coach)
        if status is None or status == "UNAVAILABLE":
            return []

    return [replace(
        state,
        current_station=nxt.station_code,
        arrival_datetime=arrive_at,
        cumulative_time_minutes=state.cumulative_time_minutes + minutes,
    )]


def _transfer_successors(state: JourneyState, query: UserQuery, store: RailDataStore) -> list[JourneyState]:
    train_number = state.current_train
    assert train_number is not None
    if state.transfer_count + 1 > query.max_transfers:
        return []
    if state.current_coach is None:
        return []  # nothing to transfer *from*
    if not _class_allowed(state.current_class, query):
        return []  # the ticket being closed would itself violate the hard constraint
    if not store.runs_on_date(train_number, query.travel_date.isoformat()):
        return []

    stops = store.get_real_halt_stops(train_number)
    idx = _route_index(stops, state.current_station)
    if idx is None or idx == len(stops) - 1:
        return []
    here, nxt = stops[idx], stops[idx + 1]

    origin = _ticket_origin(state, query)
    if origin == here.station_code:
        return []  # at the boarding station: coach choice is the board move's job
    if here.halt_minutes is None or here.halt_minutes < MIN_COACH_SWITCH_MINUTES:
        return []
    if nxt.station_code in state.visited_stations:
        return []

    ticket = _close_ticket(state, query, store, stops)

    onward = store.get_availability(train_number, here.station_code, nxt.station_code)
    composition = store.get_coach_composition(train_number)
    out: list[JourneyState] = []
    for coach, cls, _status in best_coach_per_class(
        onward, composition, exclude=state.current_coach, near=state.current_coach
    ):
        if not _class_allowed(cls, query):
            continue
        out.append(replace(
            state,
            current_coach=coach,
            current_class=cls,
            tickets_so_far=state.tickets_so_far + (ticket,),
            transfer_count=state.transfer_count + 1,
        ))
    return out
