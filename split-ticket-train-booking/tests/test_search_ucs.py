"""Tests for src.search_ucs on real 12658 data."""

from __future__ import annotations

import datetime as dt

from src.search_ucs import ucs_search
from src.state import UserQuery
from tests.search_checks import assert_no_solution, assert_valid_mail_itinerary

DATE = dt.date(2026, 9, 16)


def test_ucs_finds_sbc_to_mas(store, mail_query):
    goal, stats = ucs_search(mail_query, store)
    assert_valid_mail_itinerary(goal, stats, mail_query, store)
    assert stats.algorithm == "UCS"


def test_ucs_path_cost_is_monotone_nondecreasing(store, mail_query):
    _, stats = ucs_search(mail_query, store)
    costs = [s.cumulative_time_minutes for s in stats.path]
    assert costs == sorted(costs)
    assert costs[0] == 0.0 and costs[-1] == 340


def test_ucs_is_optimal_under_any_transfer_budget(store):
    # Optimal moving time SBC->MAS on 12658 is 340 regardless of transfer budget,
    # since a same-train transfer costs no moving time.
    for k in (0, 1, 2, 5):
        q = UserQuery("SBC", "MAS", DATE, max_transfers=k)
        goal, stats = ucs_search(q, store)
        assert goal is not None and stats.total_cost == 340


def test_ucs_no_solution_impossible_class(store):
    q = UserQuery("SBC", "MAS", DATE, travel_class_preference="CC", class_is_hard_constraint=True)
    goal, stats = ucs_search(q, store)
    assert_no_solution(goal, stats, min_expanded=1)


def test_ucs_no_solution_unreachable_destination_fails_fast(store):
    q = UserQuery("SBC", "MYS", DATE, max_transfers=2)
    goal, stats = ucs_search(q, store)
    assert_no_solution(goal, stats, min_expanded=1)
    assert stats.nodes_generated == 0


def test_ucs_no_solution_searches_exhaustively_when_connection_is_missing(store):
    from src.successors_different_train import get_successors_different_train
    q = UserQuery("PURI", "CDG", dt.date(2026, 9, 16), max_transfers=2)
    goal, stats = ucs_search(q, store, successors_fn=get_successors_different_train)
    assert_no_solution(goal, stats, min_expanded=50)
