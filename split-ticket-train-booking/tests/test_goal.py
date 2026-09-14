"""Tests for src.goal.is_goal."""

from __future__ import annotations

import datetime as dt

from src.goal import is_goal
from src.state import JourneyState, UserQuery, initial_state

QUERY = UserQuery(origin_station="SBC", destination_station="MAS", travel_date=dt.date(2026, 9, 16))


def _state(station: str, train: str | None) -> JourneyState:
    return JourneyState(
        current_station=station,
        arrival_datetime=dt.datetime(2026, 9, 17, 4, 40),
        current_train=train,
        current_coach="S1" if train else None,
        current_class="SL" if train else None,
        tickets_so_far=(),
        transfer_count=0,
        passenger_count=1,
        cumulative_time_minutes=340.0,
    )


def test_initial_state_is_not_goal():
    assert is_goal(initial_state(QUERY), QUERY) is False


def test_mid_journey_is_not_goal():
    assert is_goal(_state("KPD", "12658"), QUERY) is False


def test_at_destination_on_train_is_goal():
    assert is_goal(_state("MAS", "12658"), QUERY) is True


def test_at_destination_but_never_boarded_is_not_goal():
    # origin == destination edge case: standing at MAS without a train is not a journey
    assert is_goal(_state("MAS", None), QUERY) is False


def test_goal_depends_on_query_destination():
    other = UserQuery(origin_station="SBC", destination_station="KPD", travel_date=dt.date(2026, 9, 16))
    assert is_goal(_state("KPD", "12658"), other) is True
    assert is_goal(_state("MAS", "12658"), other) is False
