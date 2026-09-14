"""Assertions shared by the per-algorithm search tests (not a test module itself)."""

from __future__ import annotations

import datetime as dt

from src.data_store import RailDataStore
from src.goal import is_goal
from src.search_stats import SearchStats
from src.state import JourneyState, UserQuery, initial_state

MAIL = "12658"


def assert_valid_mail_itinerary(goal: JourneyState | None, stats: SearchStats, query: UserQuery, store: RailDataStore) -> None:
    """The returned path is a real, contiguous SBC -> MAS ride on 12658."""
    assert goal is not None and stats.path_found
    assert is_goal(goal, query)
    halts = [s.station_code for s in store.get_real_halt_stops(MAIL)]

    # path shape: initial -> board -> 7 continues (+ any transfers) -> goal
    path = stats.path
    assert path is not None and path[0] == initial_state(query) and path[-1] == goal
    assert all(s.current_train == MAIL for s in path[1:])
    stations = [s.current_station for s in path]
    assert stations[0] == "SBC" and stations[-1] == "MAS"
    assert set(stations) <= set(halts)
    # stations along the path are non-decreasing in route order (equal on a transfer)
    idx = [halts.index(s) for s in stations]
    assert idx == sorted(idx)

    # tickets cover the whole route contiguously, all on 12658, only real halts, all bookable
    tickets = goal.tickets_so_far
    assert tickets, "goal state must have its final ticket closed"
    assert tickets[0].from_station == "SBC" and tickets[-1].to_station == "MAS"
    for a, b in zip(tickets, tickets[1:]):
        assert a.to_station == b.from_station
    for t in tickets:
        assert t.train_number == MAIL
        assert t.from_station in halts and t.to_station in halts
        assert halts.index(t.from_station) < halts.index(t.to_station)
        assert t.status in {"CONFIRMED", "RAC", "WAITLIST"}
        assert t.coach is not None and t.travel_class is not None
    assert len(tickets) == goal.transfer_count + 1
    assert tickets[0].boarding_datetime == dt.datetime(2026, 9, 16, 22, 45)
    assert tickets[-1].alighting_datetime == dt.datetime(2026, 9, 17, 4, 40)

    # cost bookkeeping
    assert stats.total_cost == goal.cumulative_time_minutes == 340
    assert stats.nodes_expanded > 0 and stats.nodes_generated >= stats.nodes_expanded - 1
    assert stats.max_frontier_size >= 1 and stats.wall_clock_seconds > 0


def assert_no_solution(goal: JourneyState | None, stats: SearchStats, *, min_expanded: int = 1) -> None:
    assert goal is None
    assert stats.path_found is False and stats.path is None and stats.total_cost is None
    assert stats.nodes_expanded >= min_expanded
    assert stats.wall_clock_seconds > 0
