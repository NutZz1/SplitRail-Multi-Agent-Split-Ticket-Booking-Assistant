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
def agent(store, heuristic, pool):
    """Pool-backed agent: searches run in worker processes."""
    return SameTrainSearchAgent(store, heuristic, pool=pool)


@pytest.fixture(scope="module")
def thread_agent(store, heuristic):
    """Pool-less agent: searches run in a thread (non-blocking, but GIL-bound)."""
    return SameTrainSearchAgent(store, heuristic)


# --------------------------------------------------------------------------- #
# solvable query
# --------------------------------------------------------------------------- #
def test_propose_finds_sbc_to_mas(agent, mail_query):
    p = asyncio.run(agent.propose(mail_query))
    assert isinstance(p, ItineraryProposal)
    assert p.found and p.failure_reason is None
    assert p.agent_name == "SameTrainSearchAgent" and p.query == mail_query
    best = p.best
    assert best is not None and best.rank == 0 and best.agent_name == p.agent_name
    assert best.tickets and best.tickets[0].from_station == "SBC" and best.tickets[-1].to_station == "MAS"
    assert all(t.train_number == "12658" for t in best.tickets)
    assert best.total_time_minutes == 340  # same optimum Step 4's A* test established
    assert best.transfer_count == 0 and len(best.tickets) == 1
    assert best.goal_state.current_station == "MAS"
    assert 1 <= len(p.candidates) <= 3
    print("\n" + p.describe())


def test_search_stats_carried_through_unchanged(agent, store, heuristic, mail_query):
    p = asyncio.run(agent.propose(mail_query))
    _, direct = astar_search(mail_query, store, heuristic)
    for field in ("nodes_expanded", "nodes_generated", "max_frontier_size", "path_found", "total_cost", "algorithm"):
        assert getattr(p.search_stats, field) == getattr(direct, field), field
    assert p.search_stats.nodes_expanded > 0
    assert p.search_stats.path[-1] == p.best.goal_state


# --------------------------------------------------------------------------- #
# no-solution queries and failure reasons
# --------------------------------------------------------------------------- #
def test_propose_impossible_class_returns_not_found(agent):
    q = UserQuery("SBC", "MAS", DATE, travel_class_preference="CC", class_is_hard_constraint=True)
    p = asyncio.run(agent.propose(q))
    assert p.found is False
    assert p.candidates == () and p.best is None
    assert isinstance(p.failure_reason, str) and p.failure_reason
    assert "CC" in p.failure_reason and "not offered" in p.failure_reason
    assert p.search_stats.path_found is False
    print("\n" + p.describe())


@pytest.mark.parametrize("query,expect", [
    (UserQuery("ZZZZZ", "MAS", DATE), "No train has a real halt"),
    (UserQuery("SBC", "MAS", dt.date(2026, 9, 26)), "runs on 2026-09-26 (SAT)"),
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
def _best_of(fn, n: int = 5) -> float:
    best = float("inf")
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t0)
    return best


def _concurrency_numbers(agent, query):
    asyncio.run(agent.propose(query))  # warm caches / workers
    single = _best_of(lambda: asyncio.run(agent.propose(query)))

    async def two():
        return await asyncio.gather(agent.propose(query), agent.propose(query))

    concurrent = _best_of(lambda: asyncio.run(two()))
    sequential = _best_of(lambda: (asyncio.run(agent.propose(query)), asyncio.run(agent.propose(query))))
    print(
        f"\n  single propose():            {single * 1000:7.1f} ms"
        f"\n  two sequential propose():    {sequential * 1000:7.1f} ms  ({sequential / single:.2f}x single)"
        f"\n  two concurrent via gather(): {concurrent * 1000:7.1f} ms  ({concurrent / single:.2f}x single, "
        f"{concurrent / sequential:.2f}x sequential)"
    )
    return single, sequential, concurrent


def test_two_proposals_run_concurrently_with_process_pool(agent, mail_query):
    print("\n[SameTrainSearchAgent, process pool]")
    single, sequential, concurrent = _concurrency_numbers(agent, mail_query)
    # Two searches in two worker processes must finish clearly faster than
    # running them back to back; ideal is 0.5x, measured ~0.55-0.7x.
    assert concurrent < 0.85 * sequential, f"concurrent {concurrent:.3f}s vs sequential {sequential:.3f}s"


def test_thread_mode_is_nonblocking_but_does_not_overlap(thread_agent, mail_query):
    # Documented limitation, measured rather than assumed: with the DB opened
    # immutable=1 nothing in a search releases the GIL, so two threaded
    # searches take at least as long as two sequential ones. The event-loop
    # test below shows thread mode still never blocks the loop.
    print("\n[SameTrainSearchAgent, thread mode]")
    single, sequential, concurrent = _concurrency_numbers(thread_agent, mail_query)
    assert concurrent > 0.9 * sequential


def test_gather_two_different_queries_both_correct(agent, mail_query):
    q_bad = UserQuery("SBC", "MAS", DATE, travel_class_preference="CC", class_is_hard_constraint=True)

    async def both():
        return await asyncio.gather(agent.propose(mail_query), agent.propose(q_bad))

    good, bad = asyncio.run(both())
    assert good.found and good.query == mail_query and good.best.total_time_minutes == 340
    assert not bad.found and bad.query == q_bad


@pytest.mark.parametrize("mode", ["pool", "thread"])
def test_propose_does_not_block_the_event_loop(agent, thread_agent, mail_query, mode):
    # A ticking coroutine must keep making progress while propose() runs -- in both modes.
    agent = agent if mode == "pool" else thread_agent
    ticks = 0

    async def ticker(stop: asyncio.Event):
        nonlocal ticks
        while not stop.is_set():
            ticks += 1
            await asyncio.sleep(0)  # pure yield: Windows timers are ~15 ms coarse

    async def main():
        stop = asyncio.Event()
        t = asyncio.create_task(ticker(stop))
        p = await agent.propose(mail_query)
        stop.set()
        await t
        return p

    p = asyncio.run(main())
    assert p.found
    # Pool mode: the loop is free and spins thousands of times. Thread mode:
    # the loop is not blocked but is GIL-starved (it gets a turn every ~5 ms
    # switch interval), so only a handful of ticks happen during a ~25 ms search.
    minimum = 100 if mode == "pool" else 1
    assert ticks >= minimum, f"event loop was blocked: {ticks} tick(s) during the search ({mode} mode)"


def test_search_exceptions_propagate_thread_mode(store):
    class BrokenHeuristic:
        def estimate(self, a, b):
            raise RuntimeError("boom")

    broken = SameTrainSearchAgent(store, BrokenHeuristic())  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(broken.propose(UserQuery("SBC", "MAS", DATE)))


def test_search_exceptions_propagate_pool_mode(pool, store, heuristic):
    # An error inside a worker process surfaces in the awaiting coroutine.
    agent = SameTrainSearchAgent(store, heuristic, pool=pool)
    with pytest.raises(AttributeError, match="destination_station"):
        asyncio.run(agent.propose("not a query"))  # type: ignore[arg-type]


def test_pool_rejects_unknown_mode(pool, mail_query):
    with pytest.raises(ValueError, match="unknown search mode"):
        asyncio.run(pool.search(mail_query, "teleport"))


# --------------------------------------------------------------------------- #
# proposal helpers
# --------------------------------------------------------------------------- #
def test_proposal_is_frozen(agent, mail_query):
    p = asyncio.run(agent.propose(mail_query))
    with pytest.raises(Exception):
        p.found = False  # type: ignore[misc]


def test_proposal_risk_flag(agent, mail_query):
    p = asyncio.run(agent.propose(mail_query))
    assert p.best.is_risky == any(t.status in {"RAC", "WAITLIST"} for t in p.best.tickets)
