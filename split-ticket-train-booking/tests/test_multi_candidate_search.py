"""
k-candidate search on real data.

The point of this step: because g(n) is moving time only, a same-train
split ties with the direct ride, so Stage 1 must surface more than the
first goal A* happens to pop. These tests prove both known examples now
yield genuine alternatives, that the cap and the de-duplication rule work,
and that the k=1 path is unchanged.
"""

from __future__ import annotations

import asyncio
import datetime as dt

import pytest

from src.agents.different_train_search_agent import DifferentTrainSearchAgent
from src.agents.different_train_search_agent import K_CANDIDATES as K_DIFF
from src.agents.same_train_search_agent import K_CANDIDATES, SameTrainSearchAgent
from src.proposal import CandidateItinerary, ItineraryProposal, itinerary_signature
from src.search_astar import astar_search, astar_search_k
from src.state import UserQuery
from src.successors_different_train import get_successors_different_train

WED, TUE = dt.date(2026, 9, 16), dt.date(2026, 9, 15)


def _print(p: ItineraryProposal) -> None:
    print("\n" + p.describe())
    for c in p.candidates:
        print(f"     rank={c.rank} total_time_minutes={c.total_time_minutes:.0f} transfer_count={c.transfer_count} "
              f"splits={list(c.split_stations)} trains={list(c.trains)}")


# --------------------------------------------------------------------------- #
# SBC -> MAS: direct AND the BNC split, tied on time
# --------------------------------------------------------------------------- #
def test_sbc_mas_surfaces_direct_and_bnc_split(store, heuristic, mail_query):
    p = asyncio.run(SameTrainSearchAgent(store, heuristic).propose(mail_query))
    _print(p)
    assert p.found and K_CANDIDATES == 3
    by_transfers = {c.transfer_count: c for c in p.candidates}
    assert 0 in by_transfers and 1 in by_transfers, "expected both the direct ride and a split"
    direct, split = by_transfers[0], by_transfers[1]
    assert direct.total_time_minutes == split.total_time_minutes == 340   # the tie that motivated this step
    assert direct.split_stations == () and split.split_stations == ("BNC",)
    assert [t.train_number for t in split.tickets] == ["12658", "12658"]
    assert direct.rank == 0                                                # cheapest first, then fewest moves
    # BNC is the only halt >= 5 min on 12658, so exactly these two signatures exist
    assert len(p.candidates) == 2


def test_sbc_mas_candidates_via_process_pool(store, heuristic, pool, mail_query):
    # k crosses the process boundary
    p = asyncio.run(SameTrainSearchAgent(store, heuristic, pool=pool).propose(mail_query))
    assert sorted(c.transfer_count for c in p.candidates) == [0, 1]


# --------------------------------------------------------------------------- #
# PURI -> CDG: the NDLS connection is still there
# --------------------------------------------------------------------------- #
def test_puri_cdg_connection_still_among_candidates(store, heuristic):
    q = UserQuery("PURI", "CDG", TUE, max_transfers=2)
    p = asyncio.run(DifferentTrainSearchAgent(store, heuristic).propose(q))
    _print(p)
    assert p.found and K_DIFF == 3
    wanted = (("12801", "PURI", "NDLS"), ("12217", "NDLS", "CDG"))
    assert wanted in {c.signature for c in p.candidates}
    assert p.best.signature == wanted


# --------------------------------------------------------------------------- #
# cap and distinctness
# --------------------------------------------------------------------------- #
def test_candidates_capped_at_k_when_more_exist(store, heuristic):
    # 12801 PURI -> NDLS has 12 halts >= 5 min: direct + 12 single-split signatures = 13.
    q = UserQuery("PURI", "NDLS", TUE, max_transfers=1)
    all_goals, _ = astar_search_k(q, store, heuristic, k=50)
    assert len(all_goals) == 13, [g.tickets_so_far[0].to_station for g in all_goals]
    p = asyncio.run(SameTrainSearchAgent(store, heuristic).propose(q))
    _print(p)
    assert len(p.candidates) == K_CANDIDATES == 3
    assert [c.rank for c in p.candidates] == [0, 1, 2]
    # the K returned are the first K of the full list (cheapest, then discovery order)
    assert [c.signature for c in p.candidates] == [itinerary_signature(g.tickets_so_far) for g in all_goals[:3]]


def test_candidates_are_distinct_by_signature_and_cost_ordered(store, heuristic):
    q = UserQuery("PURI", "NDLS", TUE, max_transfers=1)
    goals, _ = astar_search_k(q, store, heuristic, k=13)
    sigs = [itinerary_signature(g.tickets_so_far) for g in goals]
    assert len(sigs) == len(set(sigs))                         # no duplicates
    costs = [g.cumulative_time_minutes for g in goals]
    assert costs == sorted(costs)                              # k-best A* pops goals cheapest first
    # every candidate differs in split stations / transfer count / trains, never only in coach
    keys = {(tuple(t.to_station for t in g.tickets_so_far[:-1]), g.transfer_count,
             tuple(t.train_number for t in g.tickets_so_far)) for g in goals}
    assert len(keys) == len(goals)


def test_dedup_collapses_coach_and_class_variants(store, heuristic, mail_query):
    """The documented rule: same legs (train, from, to) => same candidate, whatever the coach/class."""
    raw, _ = astar_search_k(mail_query, store, heuristic, k=50, signature_fn=lambda g: g)  # identity: no de-dup
    deduped, _ = astar_search_k(mail_query, store, heuristic, k=50)
    print(f"\nSBC->MAS raw goal states: {len(raw)}  ->  distinct itineraries: {len(deduped)}")
    assert len(raw) > len(deduped) == 2
    assert {itinerary_signature(g.tickets_so_far) for g in raw} == {itinerary_signature(g.tickets_so_far) for g in deduped}
    # the raw goals really do differ only in coach/class
    for g in raw:
        assert g.cumulative_time_minutes == 340
    coaches = {tuple(t.coach for t in g.tickets_so_far) for g in raw}
    assert len(coaches) == len(raw)


def test_first_goal_per_signature_is_kept(store, heuristic, mail_query):
    # de-duplication keeps the FIRST goal popped per signature -- the cheapest, then earliest discovered
    raw, _ = astar_search_k(mail_query, store, heuristic, k=50, signature_fn=lambda g: g)
    deduped, _ = astar_search_k(mail_query, store, heuristic, k=50)
    first_seen = {}
    for g in raw:
        first_seen.setdefault(itinerary_signature(g.tickets_so_far), g)
    assert deduped == list(first_seen.values())


# --------------------------------------------------------------------------- #
# k = 1 is the old search, exactly
# --------------------------------------------------------------------------- #
def test_k1_matches_astar_search(store, heuristic, mail_query):
    goal, stats = astar_search(mail_query, store, heuristic)
    goals, stats_k = astar_search_k(mail_query, store, heuristic, k=1)
    assert goals == [goal]
    assert (stats.nodes_expanded, stats.nodes_generated, stats.total_cost) == (stats_k.nodes_expanded, stats_k.nodes_generated, stats_k.total_cost)
    assert stats.nodes_expanded == 113   # unchanged from Step 4/6


def test_k_search_stats_are_one_aggregate_record(store, heuristic, mail_query):
    goals, stats = astar_search_k(mail_query, store, heuristic, k=3)
    assert len(goals) == 2
    assert stats.path_found and stats.path[-1] == goals[0] and stats.total_cost == goals[0].cumulative_time_minutes
    assert stats.nodes_expanded >= 113     # continued past the first goal


def test_k_must_be_positive(store, heuristic, mail_query):
    with pytest.raises(ValueError):
        astar_search_k(mail_query, store, heuristic, k=0)


# --------------------------------------------------------------------------- #
# proposal shape
# --------------------------------------------------------------------------- #
def test_no_solution_shape(store, heuristic):
    p = asyncio.run(SameTrainSearchAgent(store, heuristic).propose(UserQuery("SBC", "MYS", WED)))
    assert p.found is False and p.candidates == () and p.best is None
    assert isinstance(p.failure_reason, str) and "SBC -> MYS" in p.failure_reason
    assert p.search_stats.path_found is False


def test_candidate_fields_and_attribution(store, heuristic, mail_query):
    p = asyncio.run(SameTrainSearchAgent(store, heuristic).propose(mail_query))
    for i, c in enumerate(p.candidates):
        assert isinstance(c, CandidateItinerary)
        assert c.agent_name == "SameTrainSearchAgent" and c.rank == i
        assert c.tickets == c.goal_state.tickets_so_far
        assert c.total_time_minutes == c.goal_state.cumulative_time_minutes
        assert c.transfer_count == c.goal_state.transfer_count == len(c.tickets) - 1
        assert c.tickets[0].from_station == "SBC" and c.tickets[-1].to_station == "MAS"
    assert p.found is True and p.failure_reason is None
    with pytest.raises(Exception):
        p.candidates = ()  # type: ignore[misc]


def test_different_train_agent_also_returns_split_candidates_when_they_exist(store, heuristic, mail_query):
    # The different-train generator never switches coach, so for SBC -> MAS it
    # has exactly one signature: the direct ride. Only the same-train agent
    # proposes the BNC split -- the two agents' candidate sets are complementary.
    p = asyncio.run(DifferentTrainSearchAgent(store, heuristic).propose(mail_query))
    assert [c.transfer_count for c in p.candidates] == [0]
