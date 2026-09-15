"""
CoordinatorAgent on real data: Stage 1 || -> prune -> Stage 2 || -> rank.

Spies wrap the real agents so tests can assert on the exact query objects
passed in, on call counts, and (by injecting delays) on whether calls were
gathered concurrently or awaited one after another.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import time

import pytest

from src.agents.coordinator_agent import (
    SAME_TRAIN_MAX_TRANSFERS_CAP,
    SCORE_EPSILON,
    W_FARE,
    W_LAYOVER,
    W_TIME,
    W_TRANSFER,
    CoordinatorAgent,
    combine_failure_reasons,
    dedupe_across_agents,
    rank_candidates,
    same_train_query,
)
from src.agents.different_train_search_agent import DifferentTrainSearchAgent
from src.agents.fare_time_agent import FareTimeAgent, FareTimeScore
from src.agents.same_train_search_agent import SameTrainSearchAgent
from src.agents.seat_transfer_agent import SeatTransferAgent, TransferFeasibilityScore
from src.final_recommendation import FinalRecommendation, RankedItinerary
from src.proposal import CandidateItinerary, ItineraryProposal
from src.search_stats import SearchStats
from src.state import Ticket, UserQuery

WED, TUE = dt.date(2026, 9, 16), dt.date(2026, 9, 15)
SBC_MAS = UserQuery("SBC", "MAS", WED, max_transfers=2)
PURI_CDG = UserQuery("PURI", "CDG", TUE, max_transfers=2)


# --------------------------------------------------------------------------- #
# spies
# --------------------------------------------------------------------------- #
class SpySearch:
    """Wraps a search agent: records every query it was given; optional delay."""

    def __init__(self, inner, delay: float = 0.0):
        self.inner, self.delay, self.calls = inner, delay, []
        self.name = inner.name

    async def propose(self, query):
        self.calls.append(query)
        if self.delay:
            await asyncio.sleep(self.delay)
        return await self.inner.propose(query)


class SpyScorer:
    """Wraps a scoring agent: counts evaluate() calls; optional delay."""

    def __init__(self, inner, delay: float = 0.0):
        self.inner, self.delay, self.calls = inner, delay, []
        self.name = inner.name

    async def evaluate(self, candidate):
        self.calls.append(candidate)
        if self.delay:
            await asyncio.sleep(self.delay)
        return await self.inner.evaluate(candidate)


class StubSearch:
    """Returns a fixed proposal (used to inject synthetic candidates)."""

    def __init__(self, name, proposal):
        self.name, self.proposal, self.calls = name, proposal, []

    async def propose(self, query):
        self.calls.append(query)
        return self.proposal


@pytest.fixture(scope="module")
def agents(store, heuristic):
    return (
        SameTrainSearchAgent(store, heuristic),
        DifferentTrainSearchAgent(store, heuristic),
        SeatTransferAgent(store),
        FareTimeAgent(store),
    )


@pytest.fixture(scope="module")
def coordinator(agents, store):
    return CoordinatorAgent(*agents, store=store)


# --------------------------------------------------------------------------- #
# SBC -> MAS: direct vs BNC split, ranked
# --------------------------------------------------------------------------- #
def test_sbc_mas_ranks_direct_above_bnc_split(coordinator):
    r = asyncio.run(coordinator.resolve(SBC_MAS))
    print("\n" + r.describe())
    assert isinstance(r, FinalRecommendation) and r.found and r.failure_reason is None
    by_transfers = {o.candidate.transfer_count: o for o in r.ranked_options}
    assert set(by_transfers) == {0, 1}, "expected the direct ride AND the BNC split"
    direct, split = by_transfers[0], by_transfers[1]
    assert split.candidate.split_stations == ("BNC",)
    for o in (direct, split):
        assert o.fare_time_score.moving_time_minutes == 340
        print(f"   {o.candidate.transfer_count} transfer(s): final_score={o.final_score:.1f}  "
              f"fare={o.fare_time_score.total_fare}  penalty={o.transfer_score.total_feasibility_penalty}")
    # Both move 340 min with 15 min dwell. The split saves a few rupees (SL for
    # the first leg) but carries a real BNC transfer penalty (coach walk + 23:00
    # night switch) worth W_TRANSFER x penalty rupees; that dwarfs the saving.
    assert direct.final_score < split.final_score
    assert r.ranked_options[0] is direct
    assert direct.final_score == pytest.approx(
        W_TIME * 340 + W_FARE * direct.fare_time_score.total_fare + W_LAYOVER * 15)
    assert split.final_score == pytest.approx(
        W_TIME * 340 + W_FARE * split.fare_time_score.total_fare
        + W_TRANSFER * split.transfer_score.total_feasibility_penalty + W_LAYOVER * 15)


def test_sbc_mas_direct_found_by_both_agents_is_shown_once(coordinator):
    r = asyncio.run(coordinator.resolve(SBC_MAS))
    assert r.total_candidates_considered == 3          # 2 from same-train + 1 from different-train
    assert r.duplicate_candidates_merged == 1           # the direct ride, found by both
    assert len(r.ranked_options) == 2
    assert len({o.candidate.signature for o in r.ranked_options}) == 2
    assert r.candidates_pruned_by_hard_constraint == 0  # Stage 1 already enforced everything


# --------------------------------------------------------------------------- #
# PURI -> CDG
# --------------------------------------------------------------------------- #
def test_puri_cdg_connection_ranked_with_real_fare_and_layover(coordinator):
    t0 = time.perf_counter()
    r = asyncio.run(coordinator.resolve(PURI_CDG))
    elapsed = time.perf_counter() - t0
    print("\n" + r.describe() + f"\n   resolve() took {elapsed * 1000:.0f} ms")
    assert r.found
    best = r.best
    assert best.candidate.signature == (("12801", "PURI", "NDLS"), ("12217", "NDLS", "CDG"))
    assert best.candidate.agent_name == "DifferentTrainSearchAgent"
    f, t = best.fare_time_score, best.transfer_score
    assert f.total_fare == pytest.approx(1060, abs=5)
    assert f.layover_minutes == pytest.approx(533, abs=1)
    assert t.per_transfer_penalties[0]["kind"] == "different_train"
    assert best.final_score == pytest.approx(
        W_TIME * f.moving_time_minutes + W_FARE * f.total_fare + W_TRANSFER * t.total_feasibility_penalty + W_LAYOVER * f.layover_minutes)
    assert elapsed < 5.0, f"resolve() took {elapsed:.1f}s -- unexpectedly slow for the different-train path"


# --------------------------------------------------------------------------- #
# same-train max_transfers cap (asserted on the query object, not timing)
# --------------------------------------------------------------------------- #
def test_same_train_query_is_capped_down_never_raised():
    assert SAME_TRAIN_MAX_TRANSFERS_CAP == 1
    assert same_train_query(UserQuery("PURI", "NDLS", TUE, max_transfers=2)).max_transfers == 1
    assert same_train_query(UserQuery("PURI", "NDLS", TUE, max_transfers=5)).max_transfers == 1
    assert same_train_query(UserQuery("PURI", "NDLS", TUE, max_transfers=1)).max_transfers == 1
    assert same_train_query(UserQuery("PURI", "NDLS", TUE, max_transfers=0)).max_transfers == 0
    q = UserQuery("PURI", "NDLS", TUE, max_transfers=1)
    assert same_train_query(q) is q  # untouched, not even copied


def test_slow_same_train_path_is_capped_via_spy(agents, store):
    same, diff, seat, fare = agents
    spy_same, spy_diff = SpySearch(same), SpySearch(diff)
    coord = CoordinatorAgent(spy_same, spy_diff, seat, fare, store=store)  # type: ignore[arg-type]
    user_query = UserQuery("PURI", "NDLS", TUE, max_transfers=2)           # the known-slow request
    t0 = time.perf_counter()
    r = asyncio.run(coord.resolve(user_query))
    elapsed = time.perf_counter() - t0
    print(f"\nPURI->NDLS (user max_transfers=2) resolve() in {elapsed * 1000:.0f} ms; "
          f"same-train saw max_transfers={spy_same.calls[0].max_transfers}, different-train saw {spy_diff.calls[0].max_transfers}")
    assert len(spy_same.calls) == 1 and len(spy_diff.calls) == 1
    assert spy_same.calls[0].max_transfers == 1                # capped for the same-train agent...
    assert spy_same.calls[0] == user_query.__class__(**{**user_query.__dict__, "max_transfers": 1})
    assert spy_diff.calls[0] is user_query                     # ...passed through untouched to the other
    assert r.found and all(o.candidate.transfer_count <= 1 for o in r.ranked_options)


def test_user_conservative_max_transfers_not_altered(agents, store):
    same, diff, seat, fare = agents
    for k in (0, 1):
        spy_same, spy_diff = SpySearch(same), SpySearch(diff)
        coord = CoordinatorAgent(spy_same, spy_diff, seat, fare, store=store)  # type: ignore[arg-type]
        q = UserQuery("SBC", "MAS", WED, max_transfers=k)
        asyncio.run(coord.resolve(q))
        assert spy_same.calls[0] is q and spy_diff.calls[0] is q


# --------------------------------------------------------------------------- #
# total-failure short-circuit
# --------------------------------------------------------------------------- #
def test_total_failure_short_circuits_before_stage_2(agents, store):
    same, diff, seat, fare = agents
    spy_seat, spy_fare = SpyScorer(seat), SpyScorer(fare)
    coord = CoordinatorAgent(same, diff, spy_seat, spy_fare, store=store)  # type: ignore[arg-type]
    q = UserQuery("SBC", "MAS", WED, travel_class_preference="CC", class_is_hard_constraint=True)  # no CC anywhere
    r = asyncio.run(coord.resolve(q))
    print("\n" + r.describe())
    assert r.found is False and r.ranked_options == ()
    assert r.total_candidates_considered == 0 and r.candidates_pruned_by_hard_constraint == 0
    assert "SameTrainSearchAgent" in r.failure_reason and "DifferentTrainSearchAgent" in r.failure_reason
    assert "CC" in r.failure_reason
    assert len(spy_seat.calls) == 0 and len(spy_fare.calls) == 0   # Stage 2 never entered


def test_combine_failure_reasons_keeps_both():
    stats = SearchStats()
    a = ItineraryProposal.from_search("SameTrainSearchAgent", SBC_MAS, [], stats, failure_reason="reason A")
    b = ItineraryProposal.from_search("DifferentTrainSearchAgent", SBC_MAS, [], stats, failure_reason="reason B")
    text = combine_failure_reasons(a, b)
    assert "reason A" in text and "reason B" in text


# --------------------------------------------------------------------------- #
# concurrency proofs (injected delays make the schedule observable)
# --------------------------------------------------------------------------- #
def test_stage_1_proposes_run_concurrently(agents, store):
    same, diff, seat, fare = agents
    delay = 0.3
    spy_same, spy_diff = SpySearch(same, delay), SpySearch(diff, delay)
    coord = CoordinatorAgent(spy_same, spy_diff, seat, fare, store=store)  # type: ignore[arg-type]
    t0 = time.perf_counter()
    r = asyncio.run(coord.resolve(SBC_MAS))
    elapsed = time.perf_counter() - t0
    sequential_floor = 2 * delay
    print(f"\nStage 1 with {delay}s injected per propose(): resolve() {elapsed:.2f}s vs sequential floor {sequential_floor:.2f}s")
    assert r.found
    assert elapsed < sequential_floor * 0.9, "the two propose() calls were awaited one after another"
    assert elapsed >= delay


def test_stage_2_evaluates_all_candidates_concurrently(agents, store):
    same, diff, seat, fare = agents
    delay = 0.2
    spy_seat, spy_fare = SpyScorer(seat, delay), SpyScorer(fare, delay)
    coord = CoordinatorAgent(same, diff, spy_seat, spy_fare, store=store)  # type: ignore[arg-type]
    t0 = time.perf_counter()
    r = asyncio.run(coord.resolve(SBC_MAS))
    elapsed = time.perf_counter() - t0
    n = len(r.ranked_options)
    calls = len(spy_seat.calls) + len(spy_fare.calls)
    sequential_floor = calls * delay
    print(f"\nStage 2: {n} candidates, {calls} evaluate() calls with {delay}s each: "
          f"resolve() {elapsed:.2f}s vs sequential floor {sequential_floor:.2f}s")
    assert n >= 2 and calls == 2 * n
    assert elapsed < sequential_floor * 0.9, "evaluate() calls were awaited sequentially"
    assert elapsed >= delay


# --------------------------------------------------------------------------- #
# scoring, ranking, ties, missing fare
# --------------------------------------------------------------------------- #
def _cand(name, rank, tickets, moving, transfers):
    return CandidateItinerary(name, rank, None, tickets, moving, transfers)  # type: ignore[arg-type]


def _ticket(train, a, b, cls, dep, arr, coach="S1"):
    return Ticket(train, a, b, coach, cls, dep, arr, "CONFIRMED")


def test_rank_candidates_tie_breaking_order():
    when = dt.datetime(2026, 9, 16, 10, 0)
    tk = (_ticket("X", "A", "B", "SL", when, when + dt.timedelta(minutes=100)),)
    tk2 = (_ticket("X", "A", "C", "SL", when, when + dt.timedelta(minutes=50)),
           _ticket("X", "C", "B", "SL", when + dt.timedelta(minutes=55), when + dt.timedelta(minutes=100)))
    zero = TransferFeasibilityScore("A", True, (), 0.0)
    # three candidates engineered to the SAME final score
    c_direct = _cand("A", 0, tk, 100.0, 0)          # 2*100 + 100 fare + 0 + 1*0 = 300
    c_split = _cand("A", 1, tk2, 100.0, 1)          # same score, but 1 transfer -> loses the tie
    c_cheap = _cand("A", 2, tk, 100.0, 0)           # same score, 0 transfers, lower fare -> wins
    scores = [
        FareTimeScore("A", True, 100.0, 100.0, 100.0, 0.0),
        FareTimeScore("A", True, 100.0, 100.0, 100.0, 0.0),
        FareTimeScore("A", True, 90.0, 100.0, 110.0, 10.0),  # 2*100 + 90 + 10 = 300
    ]
    ranked = rank_candidates([c_direct, c_split, c_cheap], [zero, zero, zero], scores)
    assert [r.final_score for r in ranked] == pytest.approx([300.0, 300.0, 300.0])
    assert [r.candidate.rank for r in ranked] == [2, 0, 1]   # fare 90 < fare 100; 0 transfers < 1


def test_rank_candidates_epsilon_tie():
    when = dt.datetime(2026, 9, 16, 10, 0)
    tk = (_ticket("X", "A", "B", "SL", when, when + dt.timedelta(minutes=100)),)
    zero = TransferFeasibilityScore("A", True, (), 0.0)
    a = _cand("A", 0, tk, 100.0, 1)
    b = _cand("A", 1, tk, 100.0, 0)
    # scores differ by far less than SCORE_EPSILON -> tie -> fewer transfers wins
    fa = FareTimeScore("A", True, 100.0, 100.0, 100.0, 0.0)
    fb = FareTimeScore("A", True, 100.0 + SCORE_EPSILON / 10, 100.0, 100.0, 0.0)
    ranked = rank_candidates([a, b], [zero, zero], [fa, fb])
    assert ranked[0].candidate is b


def test_missing_fare_is_imputed_pessimistically_not_dropped(agents, store):
    """A leg ending at a coordinate-less station has no fare; the candidate must
    still be ranked, flagged, and never win because of the gap."""
    same, diff, seat, fare = agents
    bad_station = store._conn.execute("SELECT code FROM stations WHERE lat IS NULL LIMIT 1").fetchone()["code"]
    when = dt.datetime(2026, 9, 16, 22, 45)
    # a synthetic 'better' candidate whose only flaw is an unpriceable leg
    unpriceable = _cand("SameTrainSearchAgent", 0, (
        _ticket("12658", "SBC", bad_station, "SL", when, when + dt.timedelta(minutes=100)),
        _ticket("12658", bad_station, "MAS", "SL", when + dt.timedelta(minutes=110), when + dt.timedelta(minutes=300)),
    ), 290.0, 1)
    real = asyncio.run(same.propose(SBC_MAS))           # the real direct + BNC split
    stub = StubSearch("SameTrainSearchAgent", ItineraryProposal.from_search(
        "SameTrainSearchAgent", SBC_MAS, [c.goal_state for c in real.candidates], real.search_stats))
    # inject the unpriceable candidate through the different-train slot
    injected = ItineraryProposal("DifferentTrainSearchAgent", SBC_MAS, (unpriceable,), True, None, SearchStats())
    coord = CoordinatorAgent(stub, StubSearch("DifferentTrainSearchAgent", injected), seat, fare)  # type: ignore[arg-type]
    r = asyncio.run(coord.resolve(SBC_MAS))
    print("\n" + r.describe())
    assert r.found and len(r.ranked_options) == 3
    flagged = [o for o in r.ranked_options if o.fare_imputed]
    assert len(flagged) == 1 and flagged[0].candidate is unpriceable
    assert flagged[0].fare_time_score.total_fare is None
    known = [o.fare_time_score.total_fare for o in r.ranked_options if not o.fare_imputed]
    # scored as if it cost the most expensive known fare (moving 290 + penalty), never Rs 0
    expected = (W_TIME * 290 + W_FARE * max(known)
                + W_TRANSFER * flagged[0].transfer_score.total_feasibility_penalty
                + W_LAYOVER * flagged[0].fare_time_score.layover_minutes)
    assert flagged[0].final_score == pytest.approx(expected)


def test_dedupe_across_agents_keeps_first():
    when = dt.datetime(2026, 9, 16, 10, 0)
    tk = (_ticket("X", "A", "B", "SL", when, when + dt.timedelta(minutes=100)),)
    a = _cand("SameTrainSearchAgent", 0, tk, 100.0, 0)
    b = _cand("DifferentTrainSearchAgent", 0, tk, 100.0, 0)   # same legs, other agent
    c = _cand("DifferentTrainSearchAgent", 1, (_ticket("Y", "A", "B", "SL", when, when + dt.timedelta(minutes=90)),), 90.0, 0)
    assert dedupe_across_agents([a, b, c]) == [a, c]


def test_defensive_recheck_prunes_a_bad_candidate_and_reports_it(agents, store):
    """Stage 1 never produces this, so inject a candidate violating the transfer budget."""
    same, diff, seat, fare = agents
    when = dt.datetime(2026, 9, 16, 22, 45)
    over_budget = _cand("SameTrainSearchAgent", 0, (
        _ticket("12658", "SBC", "BNC", "SL", when, when + dt.timedelta(minutes=10)),
        _ticket("12658", "BNC", "KPD", "SL", when + dt.timedelta(minutes=15), when + dt.timedelta(minutes=200)),
        _ticket("12658", "KPD", "MAS", "SL", when + dt.timedelta(minutes=202), when + dt.timedelta(minutes=355)),
    ), 340.0, 2)
    q = UserQuery("SBC", "MAS", WED, max_transfers=1)
    injected = ItineraryProposal("SameTrainSearchAgent", q, (over_budget,), True, None, SearchStats())
    coord = CoordinatorAgent(StubSearch("SameTrainSearchAgent", injected), diff, seat, fare, store=store)  # type: ignore[arg-type]
    r = asyncio.run(coord.resolve(q))
    assert r.candidates_pruned_by_hard_constraint == 1
    assert all(o.candidate is not over_budget for o in r.ranked_options)
    assert r.found  # the real different-train direct ride survives


def test_all_options_returned_not_just_winner(coordinator):
    r = asyncio.run(coordinator.resolve(UserQuery("PURI", "NDLS", TUE, max_transfers=1)))
    assert len(r.ranked_options) >= 3
    scores = [o.final_score for o in r.ranked_options]
    assert scores == sorted(scores)
    assert all(isinstance(o, RankedItinerary) for o in r.ranked_options)
    assert r.best is r.ranked_options[0]
