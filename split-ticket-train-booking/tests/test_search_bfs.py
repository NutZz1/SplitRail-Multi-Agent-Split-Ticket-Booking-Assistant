"""Tests for src.search_bfs on real 12658 data."""

from __future__ import annotations

import datetime as dt

from src.search_bfs import bfs_search
from src.state import UserQuery
from tests.search_checks import assert_no_solution, assert_valid_mail_itinerary

DATE = dt.date(2026, 9, 16)


def test_bfs_finds_sbc_to_mas(store, mail_query):
    goal, stats = bfs_search(mail_query, store)
    assert_valid_mail_itinerary(goal, stats, mail_query, store)
    assert stats.algorithm == "BFS"


def test_bfs_finds_fewest_moves_path(store, mail_query):
    # BFS explores by move count: board + 7 continues = 8 moves, so the first
    # goal it pops is the zero-transfer ride. Path has 9 states (incl. initial).
    goal, stats = bfs_search(mail_query, store)
    assert len(stats.path) == 9
    assert goal.transfer_count == 0 and len(goal.tickets_so_far) == 1


def test_bfs_no_solution_impossible_class(store):
    # 12658 has no chair car; boarding yields nothing, so the search ends after
    # expanding just the initial state.
    q = UserQuery("SBC", "MAS", DATE, travel_class_preference="CC", class_is_hard_constraint=True)
    goal, stats = bfs_search(q, store)
    assert_no_solution(goal, stats, min_expanded=1)
    assert stats.nodes_generated == 0


def test_bfs_no_solution_unreachable_destination_searches_exhaustively(store):
    # 12658 is the only train boardable at SBC on the 16th and never reaches
    # MYS, so BFS must ride every branch to MAS before concluding there is no path.
    q = UserQuery("SBC", "MYS", DATE, max_transfers=2)
    goal, stats = bfs_search(q, store)
    assert_no_solution(goal, stats, min_expanded=50)
    assert stats.nodes_generated > 50


def test_bfs_no_solution_on_non_running_date(store):
    q = UserQuery("SBC", "MAS", dt.date(2026, 9, 26))
    goal, stats = bfs_search(q, store)
    assert_no_solution(goal, stats, min_expanded=1)
