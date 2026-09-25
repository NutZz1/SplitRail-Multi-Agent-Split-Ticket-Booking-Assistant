"""
Successor generation with transfers between DIFFERENT trains.

Move kinds:

``board`` / ``continue``
    Identical to the same-train generator -- reused directly from
    :mod:`src.successors` (:func:`board_successors`, :func:`continue_successors`).

``different-train transfer``
    At any real halt of the current train (including its terminus), alight,
    close the in-progress ticket, and board another train that halts here,
    runs on the travel date, departs at least ``MIN_DIFFERENT_TRAIN_BUFFER_MINUTES``
    after the current train arrives, reaches ``query.destination_station``
    later on its own real-halt route, and has a bookable coach on its next
    segment. One successor per travel class of the new train (same
    best-coach rule as boarding).

This generator deliberately does NOT produce same-train coach switches:
the two Stage-1 agents are meant to return distinct kinds of proposals, and
the same-train agent already covers coach switches.

Cost model
    g(n) remains *moving* time, consistent with every prior step and with
    the station-graph heuristic. Layover time waiting for the connecting
    train is therefore NOT charged to g; it is fully visible in
    ``arrival_datetime`` / ticket datetimes and can be ranked on later by
    the coordinator or FareTimeAgent.

Dates
    Both trains are anchored to ``query.travel_date`` as the date they leave
    their own origin (what ``train_run_dates`` records). Multi-day trains
    therefore connect correctly on the calendar (e.g. arriving NDLS on
    journey day 3 and boarding a train that also reaches NDLS on its day 3).
    A connecting train that leaves its origin on a *different* calendar day
    is not considered in this version.
"""

from __future__ import annotations

import logging
from dataclasses import replace

from src.constraint_checks import (
    best_coach_per_class,
    bookable_coaches,
    class_allowed,
    close_ticket,
    closing_candidates,
    is_bookable,
    not_revisiting,
    route_index,
    runs_on_travel_date,
    stop_datetime,
    ticket_origin,
    transfer_budget_allows,
)
from src.data_store import RailDataStore
from src.state import JourneyState, UserQuery
from src.successors import board_successors, continue_successors

logger = logging.getLogger(__name__)

#: Minimum minutes between the current train's arrival and the connecting
#: train's departure at the same station. Deliberately much stricter than
#: the same-train MIN_COACH_SWITCH_MINUTES (5): a cross-train change means
#: alighting with luggage, reading the platform indicator, crossing a foot
#: over-bridge to another platform, locating the right coach in an 18-24 coach
#: rake and boarding -- plus tolerance for the first train running a few
#: minutes late, which a same-train coach walk never has to absorb.
MIN_DIFFERENT_TRAIN_BUFFER_MINUTES: float = 20.0


def get_successors_different_train(
    state: JourneyState,
    query: UserQuery,
    store: RailDataStore,
    *,
    min_buffer_minutes: float = MIN_DIFFERENT_TRAIN_BUFFER_MINUTES,
) -> list[JourneyState]:
    """All legal next states, allowing transfers to other trains at real halts."""
    if not state.is_boarded:
        return board_successors(state, query, store)
    return continue_successors(state, query, store) + different_train_transfer_successors(
        state, query, store, min_buffer_minutes=min_buffer_minutes
    )


def different_train_transfer_successors(
    state: JourneyState,
    query: UserQuery,
    store: RailDataStore,
    *,
    min_buffer_minutes: float = MIN_DIFFERENT_TRAIN_BUFFER_MINUTES,
) -> list[JourneyState]:
    """Alight here and board a different train that will reach the destination."""
    current = state.current_train
    assert current is not None
    if not transfer_budget_allows(state, query):
        return []
    if state.current_coach is None or not class_allowed(state.current_class, query):
        return []
    if not runs_on_travel_date(store, current, query):
        return []

    here_code = state.current_station
    if ticket_origin(state, query) == here_code:
        return []  # just boarded here; switching trains now is a different board move, not a transfer

    current_stops = store.get_real_halt_stops(current)
    if route_index(current_stops, here_code) is None:
        return []
    ticket = close_ticket(state, query, store, current_stops)
    if not is_bookable(ticket.status):
        return []  # the leg ridden so far is not bookable as a ticket ending here
    destination = query.destination_station

    out: list[JourneyState] = []
    for other in store.get_trains_running_through(here_code, query.travel_date.isoformat()):
        if other == current:
            continue
        stops = store.get_real_halt_stops(other)
        idx = route_index(stops, here_code)
        if idx is None or idx == len(stops) - 1:
            continue  # passes through without halting, or terminates here
        codes = [s.station_code for s in stops]
        if destination not in codes[idx + 1:]:
            continue  # this train never reaches the destination
        here, nxt = stops[idx], stops[idx + 1]
        if not not_revisiting(nxt.station_code, state):
            continue

        depart_at = stop_datetime(here, query.travel_date, use_arrival=False)
        if depart_at is None:
            continue
        buffer = (depart_at - state.arrival_datetime).total_seconds() / 60
        if buffer < min_buffer_minutes:
            logger.debug("%s -> %s at %s: buffer %.0f min < %.0f, skipping",
                         current, other, here_code, buffer, min_buffer_minutes)
            continue

        candidates = closing_candidates(stops, idx + 1, destination)
        availability = bookable_coaches(
            store, other, here_code, candidates, query.travel_date.isoformat()
        )
        if not availability:
            continue
        composition = store.get_coach_composition(other)
        for coach, cls, _status in best_coach_per_class(availability, composition):
            if not class_allowed(cls, query):
                continue
            out.append(replace(
                state,
                arrival_datetime=depart_at,
                current_train=other,
                current_coach=coach,
                current_class=cls,
                tickets_so_far=state.tickets_so_far + (ticket,),
                transfer_count=state.transfer_count + 1,
            ))
    return out
