"""
Tests for DifferentTrainSearchAgent on real data. Mirrors the SameTrainSearchAgent tests.

Solvable multi-train query: PURI -> CDG on 2026-09-15 via the real
12801 -> 12217 connection at NDLS (400-minute buffer).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import time

import pytest

from src.agents.different_train_search_agent import DifferentTrainSearchAgent
from src.agents.same_train_search_agent import SameTrainSearchAgent
from src.proposal import ItineraryProposal
from src.search_astar import astar_search
from src.state import UserQuery
from src.successors_different_train import get_successors_different_train

TUESDAY = dt.date(2026, 9, 15)
PURI_CDG = UserQuery("PURI", "CDG", TUESDAY, max_transfers=2)


@pytest.fixture(scope="module")
def agent(store, heuristic, pool):
    return DifferentTrainSearchAgent(store, heuristic, pool=pool)


# --------------------------------------------------------------------------- #
# solvable multi-train query
# --------------------------------------------------------------------------- #
def test_propose_finds_puri_to_cdg_via_ndls(agent, store):
    p = asyncio.run(agent.propose(PURI_CDG))
    print("\n" + p.describe())
    best = p.best
    for t in best.tickets:
        print(f"   {t.describe()}  {t.boarding_datetime:%m-%d %H:%M} -> {t.alighting_datetime:%m-%d %H:%M}")
    assert isinstance(p, ItineraryProposal) and p.found and p.failure_reason is None
    assert p.agent_name == "DifferentTrainSearchAgent" and p.query == PURI_CDG
    assert [t.train_number for t in best.tickets] == ["12801", "12217"]
    assert best.tickets[0].from_station == "PURI" and best.tickets[0].to_station == "NDLS"
    assert best.tickets[1].from_station == "NDLS" and best.tickets[1].to_station == "CDG"
    assert best.transfer_count == 1
    assert best.goal_state.current_station == "CDG"
    assert best.goal_state.arrival_datetime == dt.datetime(2026, 9, 17, 15, 45)
    # real connection time at NDLS
    assert best.tickets[1].boarding_datetime - best.tickets[0].alighting_datetime == dt.timedelta(minutes=400)
    for t in best.tickets:
        assert t.status in {"CONFIRMED", "RAC", "WAITLIST"}
        assert t.coach is not None and t.travel_class is not None


def test_moving_time_is_sum_of_legs_not_layover(agent, store):
    from src.rail_graph import segment_minutes
    p = asyncio.run(agent.propose(PURI_CDG))

    def leg_minutes(train, a, b):
        stops = store.get_real_halt_stops(train)
        codes = [s.station_code for s in stops]
        i, j = codes.index(a), codes.index(b)
        return sum(segment_minutes(x, y) for x, y in zip(stops[i:j], stops[i + 1:j + 1]))

    best = p.best
    expected = sum(leg_minutes(t.train_number, t.from_station, t.to_station) for t in best.tickets)
    assert best.total_time_minutes == expected
    wall = (best.goal_state.arrival_datetime - best.tickets[0].boarding_datetime).total_seconds() / 60
    assert wall > expected + 400  # layover and dwell are in wall-clock, not in g


def test_same_train_agent_cannot_solve_this_but_different_train_can(store, heuristic, pool):
    same = asyncio.run(SameTrainSearchAgent(store, heuristic, pool=pool).propose(PURI_CDG))
    diff = asyncio.run(DifferentTrainSearchAgent(store, heuristic, pool=pool).propose(PURI_CDG))
    assert not same.found and "No single train runs PURI -> CDG" in same.failure_reason
    assert diff.found


def test_direct_route_still_found_without_a_transfer(agent, mail_query):
    p = asyncio.run(agent.propose(mail_query))
    assert p.found and p.best.transfer_count == 0 and p.best.total_time_minutes == 340
    assert [t.train_number for t in p.best.tickets] == ["12658"]


def test_search_stats_carried_through_unchanged(agent, store, heuristic):
    p = asyncio.run(agent.propose(PURI_CDG))
    _, direct = astar_search(PURI_CDG, store, heuristic, successors_fn=get_successors_different_train)
    for field in ("nodes_expanded", "nodes_generated", "max_frontier_size", "path_found", "total_cost", "algorithm"):
        assert getattr(p.search_stats, field) == getattr(direct, field), field
    assert p.search_stats.path[-1] == p.best.goal_state
    print(f"\n{p.search_stats.header()}\n{p.search_stats.row()}")


# --------------------------------------------------------------------------- #
# no-solution
# --------------------------------------------------------------------------- #
def test_no_solution_when_connection_does_not_run(agent):
    wed = UserQuery("PURI", "CDG", dt.date(2026, 9, 16), max_transfers=2)  # 12217 is Tue/Fri
    p = asyncio.run(agent.propose(wed))
    print("\n" + p.describe())
    assert not p.found and p.candidates == () and p.best is None
    assert "Boarded 12801" in p.failure_reason and "no connecting train" in p.failure_reason
    assert p.search_stats.nodes_expanded > 10  # it really searched 12801's route


def test_no_solution_transfer_budget_zero(agent):
    p = asyncio.run(agent.propose(UserQuery("PURI", "CDG", TUESDAY, max_transfers=0)))
    assert not p.found and "max_transfers=0" in p.failure_reason


@pytest.mark.parametrize("query,expect", [
    (UserQuery("ZZZZZ", "CDG", TUESDAY), "No train has a real halt"),
    (UserQuery("PURI", "CDG", dt.date(2026, 9, 26)), "runs on 2026-09-26 (SAT)"),
    (UserQuery("PURI", "CDG", TUESDAY, travel_class_preference="CC", class_is_hard_constraint=True), "no coach had availability at boarding in class CC"),
])
def test_failure_reasons(agent, query, expect):
    p = asyncio.run(agent.propose(query))
    assert not p.found and expect in p.failure_reason, p.failure_reason


# --------------------------------------------------------------------------- #
# concurrency
# --------------------------------------------------------------------------- #
def _best_of(fn, n: int = 5) -> float:
    best = float("inf")
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t0)
    return best


def test_two_proposals_run_concurrently_with_process_pool(agent):
    asyncio.run(agent.propose(PURI_CDG))
    single = _best_of(lambda: asyncio.run(agent.propose(PURI_CDG)))

    async def two():
        return await asyncio.gather(agent.propose(PURI_CDG), agent.propose(PURI_CDG))

    concurrent = _best_of(lambda: asyncio.run(two()))
    sequential = _best_of(lambda: (asyncio.run(agent.propose(PURI_CDG)), asyncio.run(agent.propose(PURI_CDG))))
    print(
        f"\n[DifferentTrainSearchAgent, process pool]"
        f"\n  single propose():            {single * 1000:7.1f} ms"
        f"\n  two sequential propose():    {sequential * 1000:7.1f} ms  ({sequential / single:.2f}x single)"
        f"\n  two concurrent via gather(): {concurrent * 1000:7.1f} ms  ({concurrent / single:.2f}x single, "
        f"{concurrent / sequential:.2f}x sequential)"
    )
    assert concurrent < 0.85 * sequential


def test_same_and_different_agents_gather_in_parallel(store, heuristic, pool, mail_query):
    """The Stage-1 pair the coordinator will run: both agents on one query, concurrently."""
    same = SameTrainSearchAgent(store, heuristic, pool=pool)
    diff = DifferentTrainSearchAgent(store, heuristic, pool=pool)

    async def both(q):
        return await asyncio.gather(same.propose(q), diff.propose(q))

    asyncio.run(both(PURI_CDG))
    seq = _best_of(lambda: (asyncio.run(same.propose(PURI_CDG)), asyncio.run(diff.propose(PURI_CDG))))
    conc = _best_of(lambda: asyncio.run(both(PURI_CDG)))
    s, d = asyncio.run(both(PURI_CDG))
    print(f"\n[Stage-1 pair on PURI->CDG] sequential {seq * 1000:.1f} ms, gathered {conc * 1000:.1f} ms ({conc / seq:.2f}x)")
    assert not s.found and d.found
    assert conc < seq  # the same-train search is short; overlap is bounded by the longer one

    s2, d2 = asyncio.run(both(mail_query))
    assert s2.found and d2.found and s2.best.total_time_minutes == d2.best.total_time_minutes == 340


def test_propose_does_not_block_the_event_loop(agent):
    ticks = 0

    async def ticker(stop: asyncio.Event):
        nonlocal ticks
        while not stop.is_set():
            ticks += 1
            await asyncio.sleep(0)  # pure yield: Windows timers are ~15 ms coarse

    async def main():
        stop = asyncio.Event()
        t = asyncio.create_task(ticker(stop))
        p = await agent.propose(PURI_CDG)
        stop.set()
        await t
        return p

    assert asyncio.run(main()).found
    assert ticks >= 100  # loop is free while the worker process searches


def test_exceptions_propagate_from_worker(agent):
    with pytest.raises(AttributeError):
        asyncio.run(agent.propose("not a query"))  # type: ignore[arg-type]
