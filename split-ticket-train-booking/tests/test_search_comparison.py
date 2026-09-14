"""
BFS vs UCS vs A* on the same real query -- the numbers for Review 1.

Run with ``pytest -s tests/test_search_comparison.py`` to see the tables.
"""

from __future__ import annotations

import datetime as dt

import pytest

from src.search_astar import astar_search
from src.search_bfs import bfs_search
from src.search_stats import SearchStats
from src.search_ucs import ucs_search
from src.state import UserQuery

DATE = dt.date(2026, 9, 16)


def run_all(query, store, heuristic) -> dict[str, tuple]:
    return {
        "BFS": bfs_search(query, store),
        "UCS": ucs_search(query, store),
        "A*": astar_search(query, store, heuristic),
    }


def print_table(title: str, results: dict[str, tuple]) -> None:
    print(f"\n{title}\n{SearchStats.header()}")
    for _, (_, stats) in results.items():
        print(stats.row())
    for name, (goal, _) in results.items():
        if goal is not None:
            print(f"  {name:<4} itinerary: " + " | ".join(t.describe() for t in goal.tickets_so_far)
                  + f"  (transfers={goal.transfer_count}, arrive {goal.arrival_datetime:%Y-%m-%d %H:%M})")


@pytest.fixture(scope="module")
def results(store, heuristic, mail_query):
    return run_all(mail_query, store, heuristic)


def test_all_three_find_a_path(results):
    for name, (goal, stats) in results.items():
        assert goal is not None and stats.path_found, name


def test_all_three_agree_on_total_cost(results):
    # UCS and A* (admissible h) are both optimal, so they must agree.
    #
    # BFS optimises move count, not time, and in general the two do NOT
    # coincide: a route with fewer segments can be slower, and vice versa.
    # They coincide HERE only because the search space is same-train-only on a
    # single boardable train: every complete SBC->MAS path is the same 7
    # segments (340 min of moving time) plus zero-cost transfers, so all goals
    # cost exactly 340 and the fewest-moves goal is trivially also optimal.
    # This assertion must be revisited once different-train transfers exist.
    costs = {name: stats.total_cost for name, (_, stats) in results.items()}
    assert costs["UCS"] == costs["A*"] == 340
    assert costs["BFS"] == costs["UCS"]


def test_astar_expands_no_more_than_ucs(results):
    astar_exp = results["A*"][1].nodes_expanded
    ucs_exp = results["UCS"][1].nodes_expanded
    print(f"\nnodes expanded: A*={astar_exp}  UCS={ucs_exp}")
    assert astar_exp <= ucs_exp


def test_comparison_table(results):
    print_table("SBC -> MAS on 2026-09-16, max_transfers=2", results)
    # every algorithm reports the same, fully-populated stats shape
    for _, stats in results.values():
        assert stats.nodes_expanded > 0 and stats.nodes_generated > 0
        assert stats.max_frontier_size > 0 and stats.wall_clock_seconds > 0
        assert stats.path and stats.total_cost is not None


def test_comparison_table_no_transfers(store, heuristic):
    q = UserQuery("SBC", "MAS", DATE, max_transfers=0)
    results = run_all(q, store, heuristic)
    print_table("SBC -> MAS on 2026-09-16, max_transfers=0", results)
    assert {stats.total_cost for _, stats in results.values()} == {340}
    assert results["A*"][1].nodes_expanded <= results["UCS"][1].nodes_expanded


def test_comparison_table_no_solution(store, heuristic):
    q = UserQuery("SBC", "MYS", DATE, max_transfers=2)
    results = run_all(q, store, heuristic)
    print_table("SBC -> MYS on 2026-09-16 (unreachable: exhaustive search, no path)", results)
    for _, (goal, stats) in results.items():
        assert goal is None and not stats.path_found and stats.nodes_expanded > 50
