"""Tests for src.search_astar on real 12658 data, including the h = inf edge case."""

from __future__ import annotations

import datetime as dt
import heapq
import math

from src.search_astar import astar_search
from src.state import UserQuery
from tests.search_checks import assert_no_solution, assert_valid_mail_itinerary

DATE = dt.date(2026, 9, 16)


def test_astar_finds_sbc_to_mas(store, heuristic, mail_query):
    goal, stats = astar_search(mail_query, store, heuristic)
    assert_valid_mail_itinerary(goal, stats, mail_query, store)
    assert stats.algorithm == "A*"


def test_astar_f_is_bounded_by_true_cost_along_path(store, heuristic, mail_query):
    # Admissibility in action: g + h never exceeds the optimal cost on the found path.
    _, stats = astar_search(mail_query, store, heuristic)
    for s in stats.path:
        f = s.cumulative_time_minutes + heuristic.estimate(s.current_station, "MAS")
        assert f <= stats.total_cost


def test_astar_no_solution_impossible_class(store, heuristic):
    q = UserQuery("SBC", "MAS", DATE, travel_class_preference="CC", class_is_hard_constraint=True)
    goal, stats = astar_search(q, store, heuristic)
    assert_no_solution(goal, stats, min_expanded=1)


def test_astar_no_solution_unreachable_destination_searches_exhaustively(store, heuristic):
    # MYS is in the graph and h(x, MYS) is finite, so A* orders the frontier
    # normally, rides 12658 to MAS and only then reports no path.
    q = UserQuery("SBC", "MYS", DATE, max_transfers=2)
    assert math.isfinite(heuristic.estimate("SBC", "MYS"))
    goal, stats = astar_search(q, store, heuristic)
    assert_no_solution(goal, stats, min_expanded=50)


def test_astar_handles_inf_heuristic_without_crashing(store, heuristic):
    # A destination the graph cannot reach makes every f = inf. The search
    # must still run to completion (exhaustively, FIFO within the inf tier)
    # and return (None, stats), not raise.
    assert heuristic.estimate("SBC", "NOT_A_STATION") == float("inf")
    q = UserQuery("SBC", "NOT_A_STATION", DATE, max_transfers=2)
    goal, stats = astar_search(q, store, heuristic)
    assert_no_solution(goal, stats, min_expanded=50)


def test_inf_f_sorts_last_in_the_heap():
    # Confirms the ordering A* relies on rather than special-casing inf.
    heap: list[tuple[float, int, str]] = []
    heapq.heappush(heap, (float("inf"), 0, "disconnected-first-pushed"))
    heapq.heappush(heap, (340.0, 1, "goal"))
    heapq.heappush(heap, (float("inf"), 2, "disconnected-second"))
    heapq.heappush(heap, (278.0, 3, "start"))
    order = [heapq.heappop(heap)[2] for _ in range(4)]
    assert order == ["start", "goal", "disconnected-first-pushed", "disconnected-second"]


def test_astar_uses_the_heuristic(store, heuristic, mail_query):
    # Every f() call goes through the goal-keyed Dijkstra cache.
    heuristic.clear_cache()
    astar_search(mail_query, store, heuristic)
    assert "MAS" in heuristic.cached_goals
