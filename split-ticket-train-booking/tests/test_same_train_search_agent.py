"""
Tests for SameTrainSearchAgent on real 12658 data.

Coroutines are driven with ``asyncio.run`` so no pytest-asyncio plugin is
needed. Run with ``-s`` to see the concurrency timings.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import time

import pytest

from src.agents.same_train_search_agent import SameTrainSearchAgent, diagnose_failure
from src.proposal import ItineraryProposal
from src.search_astar import astar_search
from src.state import UserQuery

DATE = dt.date(2026, 9, 16)


@pytest.fixture(scope="module")
def agent(store, heuristic):
    return SameTrainSearchAgent(store, heuristic)


# --------------------------------------------------------------------------- #
# solvable query
# --------------------------------------------------------------------------- #
def test_propose_finds_sbc_to_mas(agent, mail_query):
    p = asyncio.run(agent.propose(mail_query))
    assert isinstance(p, ItineraryProposal)
    assert p.found and p.failure_reason is None
    assert p.agent_name == "SameTrainSearchAgent" and p.query == mail_query
    assert p.tickets and p.tickets[0].from_station == "SBC" and p.tickets[-1].to_station == "MAS"
    assert all(t.train_number == "12658" for t in p.tickets)
    assert p.total_time_minutes == 340  # same optimum Step 4's A* test established
    assert p.transfer_count == 0 and len(p.tickets) == 1
    assert p.goal_state is not None and p.goal_state.current_station == "MAS"
    print("\n" + p.describe())


def test_search_stats_carried_through_unchanged(agent, store, heuristic, mail_query):
    p = asyncio.run(agent.propose(mail_query))
    _, direct = astar_search(mail_query, store, heuristic)
    for field in ("nodes_expanded", "nodes_generated", "max_frontier_size", "path_found", "total_cost", "algorithm"):
        assert getattr(p.search_stats, field) == getattr(direct, field), field
    assert p.search_stats.nodes_expanded == 91
    assert p.search_stats.path[-1] == p.goal_state


# --------------------------------------------------------------------------- #
# no-solution queries and failure reasons
# --------------------------------------------------------------------------- #
def test_propose_impossible_class_returns_not_found(agent):
    q = UserQuery("SBC", "MAS", DATE, travel_class_preference="CC", class_is_hard_constraint=True)
    p = asyncio.run(agent.propose(q))
    assert p.found is False
    assert p.tickets == () and p.goal_state is None
    assert p.total_time_minutes is None and p.transfer_count is None
    assert isinstance(p.failure_reason, str) and p.failure_reason
    assert "CC" in p.failure_reason and "not offered" in p.failure_reason
    assert p.search_stats.path_found is False
    print("\n" + p.describe())


@pytest.mark.parametrize("query,expect", [
    (UserQuery("ZZZZZ", "MAS", DATE), "No train has a real halt"),
    (UserQuery("SBC", "MAS", dt.date(2026, 9, 26)), "run on 2026-09-26"),
    (UserQuery("SBC", "MYS", DATE), "No single train runs SBC -> MYS"),
    (UserQuery("SBC", "MAS", DATE, travel_class_preference="CC", class_is_hard_constraint=True), "Class CC is not offered"),
])
def test_failure_reasons_are_specific(agent, query, expect):
    p = asyncio.run(agent.propose(query))
    assert not p.found
    assert expect in p.failure_reason, p.failure_reason
    print(f"\n{query.origin_station}->{query.destination_station} {query.travel_date}: {p.failure_reason}")


def test_diagnose_lumped_case_when_boarded_but_dead_ended(store, mail_query):
    # Simulate stats from a search that boarded (generated nodes) yet failed.
    from src.search_stats import SearchStats
    stats = SearchStats(nodes_expanded=40, nodes_generated=45, path_found=False)
    reason = diagnose_failure(mail_query, store, stats)
    assert "Boarded 12658" in reason and "coach switch" in reason and "40 states" in reason


# --------------------------------------------------------------------------- #
# async interface / concurrency
# --------------------------------------------------------------------------- #
def _best_of(fn, n: int = 3) -> float:
    best = float("inf")
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t0)
    return best


def test_two_proposals_run_concurrently(agent, mail_query):
    asyncio.run(agent.propose(mail_query))  # warm the heuristic cache and thread pool

    single = _best_of(lambda: asyncio.run(agent.propose(mail_query)))

    async def two():
        return await asyncio.gather(agent.propose(mail_query), agent.propose(mail_query))

    concurrent = _best_of(lambda: asyncio.run(two()))
    sequential = _best_of(lambda: (asyncio.run(agent.propose(mail_query)), asyncio.run(agent.propose(mail_query))))

    print(
        f"\nsingle propose():            {single * 1000:7.1f} ms"
        f"\ntwo sequential propose():    {sequential * 1000:7.1f} ms  ({sequential / single:.2f}x single)"
        f"\ntwo concurrent via gather(): {concurrent * 1000:7.1f} ms  ({concurrent / single:.2f}x single)"
    )
    # Real overlap: clearly under 2x. (Not ~1x: the GIL serialises the pure-Python
    # part of A*; only the sqlite query time overlaps.)
    assert concurrent < 1.8 * single


def test_gather_two_different_queries_both_correct(agent, mail_query):
    q_bad = UserQuery("SBC", "MAS", DATE, travel_class_preference="CC", class_is_hard_constraint=True)

    async def both():
        return await asyncio.gather(agent.propose(mail_query), agent.propose(q_bad))

    good, bad = asyncio.run(both())
    assert good.found and good.query == mail_query and good.total_time_minutes == 340
    assert not bad.found and bad.query == q_bad


def test_propose_does_not_block_the_event_loop(agent, mail_query):
    # A ticking coroutine must keep making progress while propose() runs.
    ticks = 0

    async def ticker(stop: asyncio.Event):
        nonlocal ticks
        while not stop.is_set():
            ticks += 1
            await asyncio.sleep(0.001)

    async def main():
        stop = asyncio.Event()
        t = asyncio.create_task(ticker(stop))
        p = await agent.propose(mail_query)
        stop.set()
        await t
        return p

    p = asyncio.run(main())
    assert p.found
    assert ticks >= 3, f"event loop was blocked: only {ticks} tick(s) during a ~40 ms search"


def test_search_exceptions_propagate(store):
    class BrokenHeuristic:
        def estimate(self, a, b):
            raise RuntimeError("boom")

    broken = SameTrainSearchAgent(store, BrokenHeuristic())  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(broken.propose(UserQuery("SBC", "MAS", DATE)))


# --------------------------------------------------------------------------- #
# proposal helpers
# --------------------------------------------------------------------------- #
def test_proposal_is_frozen(agent, mail_query):
    p = asyncio.run(agent.propose(mail_query))
    with pytest.raises(Exception):
        p.found = False  # type: ignore[misc]


def test_proposal_risk_flag(agent, mail_query):
    p = asyncio.run(agent.propose(mail_query))
    assert p.is_risky == any(t.status in {"RAC", "WAITLIST"} for t in p.tickets)
