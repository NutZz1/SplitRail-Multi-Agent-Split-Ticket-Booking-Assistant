"""Goal test for the journey search."""

from __future__ import annotations

from src.state import JourneyState, UserQuery


def is_goal(state: JourneyState, query: UserQuery) -> bool:
    """True when the passenger has *arrived* at the destination on a train.

    Being at the destination in the not-yet-boarded state (origin == destination,
    or a malformed start) does not count: a real journey must have been ridden.
    """
    return state.current_station == query.destination_station and state.current_train is not None
