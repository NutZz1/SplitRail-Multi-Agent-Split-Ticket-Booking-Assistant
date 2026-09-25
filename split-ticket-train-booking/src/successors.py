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
    A ticket covers (ticket origin -> alighting station) in one coach and
    needs exactly one bookable pair: that one. A ``continue`` move is
    allowed while the in-progress ticket can still be closed at some halt
    ahead (see :func:`src.constraint_checks.bookable_coaches`); closing it
    (transfer or destination) requires that specific pair to be bookable.
    ``UNAVAILABLE`` prunes; ``RAC``/``WAITLIST`` are allowed and surface as
    ``Ticket.status``/``Ticket.is_risky``.

Dates
    ``query.travel_date`` is the date the train departs its *origin*
    (that is what ``train_run_dates`` records). Stop datetimes are
    ``travel_date + (journey_day - 1)`` + the scheduled clock time, so an
    overnight train like 12658 arrives at MAS on the following calendar
    date automatically.

Different-train transfers are deliberately not implemented yet.
"""

from __future__ import annotations

import logging
from dataclasses import replace

from src.data_store import RailDataStore
from src.rail_graph import segment_minutes
from src.state import JourneyState, UserQuery

logger = logging.getLogger(__name__)

#: Minimum scheduled halt (minutes) for a passenger to change coach on the same train.
MIN_COACH_SWITCH_MINUTES: float = 5.0

# Shared constraint checks and helpers live in src.constraint_checks; they are
# re-exported here so existing imports (tests, agents, searches) keep working.
from src.constraint_checks import (  # noqa: E402,F401  (re-exports)
    COACH_CLASS_PREFIXES,
    STATUS_PRIORITY,
    best_coach_per_class,
    bookable_coaches,
    class_allowed as _class_allowed,
    closing_candidates,
    close_final_ticket,
    close_ticket as _close_ticket,
    coach_class,
    is_bookable,
    not_revisiting,
    route_index as _route_index,
    runs_on_travel_date,
    stop_datetime,
    ticket_origin as _ticket_origin,
    transfer_budget_allows,
)


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def get_successors(state: JourneyState, query: UserQuery, store: RailDataStore) -> list[JourneyState]:
    """All legal next states from ``state`` under ``query``'s hard constraints."""
    if not state.is_boarded:
        # This generator can never change trains, so a train that does not
        # reach the destination can never lead to a goal: don't board it.
        return board_successors(state, query, store, require_destination=True)
    return continue_successors(state, query, store) + same_train_transfer_successors(state, query, store)


# --------------------------------------------------------------------------- #
# Move generators
# --------------------------------------------------------------------------- #
def board_successors(
    state: JourneyState, query: UserQuery, store: RailDataStore, *, require_destination: bool = False
) -> list[JourneyState]:
    """Board a train halting here that runs today and has somewhere further to go.

    ``require_destination=True`` additionally demands that the train's own
    route reaches ``query.destination_station`` later on -- right for a
    same-train-only search, wrong for one that may change trains.
    """
    out: list[JourneyState] = []

    for train_number in store.get_trains_running_through(state.current_station, query.travel_date.isoformat()):
        stops = store.get_real_halt_stops(train_number)
        idx = _route_index(stops, state.current_station)
        if idx is None or idx == len(stops) - 1:
            continue  # only passes through here, or terminates here: nothing to ride
        here, nxt = stops[idx], stops[idx + 1]
        if require_destination and query.destination_station not in [s.station_code for s in stops[idx + 1:]]:
            continue
        if not not_revisiting(nxt.station_code, state):
            continue
        depart_at = stop_datetime(here, query.travel_date, use_arrival=False)
        if depart_at is None:
            continue

        candidates = closing_candidates(stops, idx + 1, query.destination_station)
        availability = bookable_coaches(
            store, train_number, here.station_code, candidates, query.travel_date.isoformat()
        )
        if not availability:
            logger.debug("board %s at %s: no bookable coach for any ticket from here, skipping",
                         train_number, here.station_code)
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


def continue_successors(state: JourneyState, query: UserQuery, store: RailDataStore) -> list[JourneyState]:
    train_number = state.current_train
    assert train_number is not None
    if not runs_on_travel_date(store, train_number, query):
        return []
    if not _class_allowed(state.current_class, query):
        return []

    stops = store.get_real_halt_stops(train_number)
    idx = _route_index(stops, state.current_station)
    if idx is None or idx == len(stops) - 1:
        return []
    here, nxt = stops[idx], stops[idx + 1]
    if not not_revisiting(nxt.station_code, state):
        return []

    minutes = segment_minutes(here, nxt)
    if minutes is None or minutes <= 0:
        logger.warning("train %s: %s->%s has unusable timing (%s), pruning",
                       train_number, here.station_code, nxt.station_code, minutes)
        return []
    arrive_at = stop_datetime(nxt, query.travel_date, use_arrival=True)
    if arrive_at is None:
        return []

    # Riding on is only useful if the in-progress ticket can still be closed
    # at some halt ahead (up to the destination) in the current coach.
    if state.current_coach is not None:
        origin = _ticket_origin(state, query)
        candidates = closing_candidates(stops, idx + 1, query.destination_station)
        if state.current_coach not in bookable_coaches(
            store, train_number, origin, candidates, query.travel_date.isoformat()
        ):
            return []

    return [replace(
        state,
        current_station=nxt.station_code,
        arrival_datetime=arrive_at,
        cumulative_time_minutes=state.cumulative_time_minutes + minutes,
    )]


def same_train_transfer_successors(state: JourneyState, query: UserQuery, store: RailDataStore) -> list[JourneyState]:
    train_number = state.current_train
    assert train_number is not None
    if not transfer_budget_allows(state, query):
        return []
    if state.current_coach is None:
        return []  # nothing to transfer *from*
    if not _class_allowed(state.current_class, query):
        return []  # the ticket being closed would itself violate the hard constraint
    if not runs_on_travel_date(store, train_number, query):
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
    if not not_revisiting(nxt.station_code, state):
        return []

    ticket = _close_ticket(state, query, store, stops)
    if not is_bookable(ticket.status):
        return []  # the segment ridden so far is not bookable as a ticket ending here

    candidates = closing_candidates(stops, idx + 1, query.destination_station)
    onward = bookable_coaches(
        store, train_number, here.station_code, candidates, query.travel_date.isoformat()
    )
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
