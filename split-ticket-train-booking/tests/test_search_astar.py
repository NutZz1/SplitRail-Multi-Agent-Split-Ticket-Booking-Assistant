"""Tests for src.search_astar on real 12658 data, including the h = inf edge case."""

from __future__ import annotations

import datetime as dt
import heapq
import math

from src.search_astar import astar_search
from src.state import UserQuery
from tests.search_checks import assert_no_solution, assert_valid_mail_itinerary
from tests.markers import requires_full_network

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


@requires_full_network
def test_astar_no_solution_unreachable_destination_fails_fast(store, heuristic):
    # MYS is in the graph and h(x, MYS) is finite, but the same-train
    # generator refuses to board 12658 (it never reaches MYS): 1 expansion.
    q = UserQuery("SBC", "MYS", DATE, max_transfers=2)
    assert math.isfinite(heuristic.estimate("SBC", "MYS"))
    goal, stats = astar_search(q, store, heuristic)
    assert_no_solution(goal, stats, min_expanded=1)
    assert stats.nodes_generated == 0


def test_astar_no_solution_searches_exhaustively_when_connection_is_missing(store, heuristic):
    from src.successors_different_train import get_successors_different_train
    q = UserQuery("PURI", "CDG", dt.date(2026, 9, 16), max_transfers=2)
    goal, stats = astar_search(q, store, heuristic, successors_fn=get_successors_different_train)
    assert_no_solution(goal, stats, min_expanded=50)


def test_astar_handles_inf_heuristic_without_crashing(store, heuristic):
    # A destination the graph cannot reach makes every f = inf. The search
    # must still run to completion (exhaustively, FIFO within the inf tier)
    # and return (None, stats), not raise.
    from src.successors_different_train import get_successors_different_train
    assert heuristic.estimate("SBC", "NOT_A_STATION") == float("inf")
    q = UserQuery("SBC", "NOT_A_STATION", DATE, max_transfers=2)
    goal, stats = astar_search(q, store, heuristic, successors_fn=get_successors_different_train)
    assert_no_solution(goal, stats, min_expanded=20)  # rides 12658 to its end, every f == inf


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


# --------------------------------------------------------------------------- #
# successors_fn parameter (added in Step 6)
# --------------------------------------------------------------------------- #
def test_astar_default_successors_fn_is_same_train(store, heuristic, mail_query):
    """Regression: with no successors_fn the search is byte-for-byte the same-train search."""
    from src.successors import get_successors
    goal_default, stats_default = astar_search(mail_query, store, heuristic)
    goal_explicit, stats_explicit = astar_search(mail_query, store, heuristic, successors_fn=get_successors)
    assert goal_default == goal_explicit
    assert (stats_default.nodes_expanded, stats_default.nodes_generated, stats_default.max_frontier_size,
            stats_default.total_cost) == (stats_explicit.nodes_expanded, stats_explicit.nodes_generated,
                                          stats_explicit.max_frontier_size, stats_explicit.total_cost)
    assert_valid_mail_itinerary(goal_default, stats_default, mail_query, store)
    assert [t.train_number for t in goal_default.tickets_so_far] == ["12658"]


def test_astar_default_successors_never_change_trains(store, heuristic):
    """Same-train default cannot solve a query that needs a train change; the plug-in generator can."""
    from src.successors_different_train import get_successors_different_train
    q = UserQuery("PURI", "CDG", dt.date(2026, 9, 15), max_transfers=2)
    goal_same, stats_same = astar_search(q, store, heuristic)
    goal_diff, _ = astar_search(q, store, heuristic, successors_fn=get_successors_different_train)
    assert goal_same is None and not stats_same.path_found
    assert goal_diff is not None and {t.train_number for t in goal_diff.tickets_so_far} == {"12801", "12217"}
