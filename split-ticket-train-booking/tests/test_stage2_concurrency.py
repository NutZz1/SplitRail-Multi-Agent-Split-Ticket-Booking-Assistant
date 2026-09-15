"""
Stage-2 agents through asyncio.gather(), the way the coordinator will call them.

These agents are lightweight (a few lookups), so no speedup is claimed or
measured; what matters is that the async interface composes cleanly with
the Stage-1 agents in a single gather.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import inspect

from src.agents.different_train_search_agent import DifferentTrainSearchAgent
from src.agents.fare_time_agent import FareTimeAgent, FareTimeScore
from src.agents.same_train_search_agent import SameTrainSearchAgent
from src.agents.seat_transfer_agent import SeatTransferAgent, TransferFeasibilityScore
from src.state import UserQuery


def test_both_scoring_agents_gather_on_one_proposal(store, bnc_split_proposal):
    seat, fare = SeatTransferAgent(store), FareTimeAgent(store)

    async def both():
        return await asyncio.gather(seat.evaluate(bnc_split_proposal), fare.evaluate(bnc_split_proposal))

    s, f = asyncio.run(both())
    assert isinstance(s, TransferFeasibilityScore) and isinstance(f, FareTimeScore)
    assert s.applicable and f.applicable
    assert s.proposal_agent_name == f.proposal_agent_name == bnc_split_proposal.agent_name
    assert s.transfer_count == 1 and f.layover_minutes > 0
    print(f"\n{s.describe()}\n{f.describe()}")


def test_scoring_many_proposals_concurrently(store, mail_proposal, bnc_split_proposal, puri_cdg_proposal, no_solution_proposal):
    seat, fare = SeatTransferAgent(store), FareTimeAgent(store)
    proposals = [mail_proposal, bnc_split_proposal, puri_cdg_proposal, no_solution_proposal]

    async def all_scores():
        return await asyncio.gather(*(seat.evaluate(p) for p in proposals), *(fare.evaluate(p) for p in proposals))

    results = asyncio.run(all_scores())
    seats, fares = results[:4], results[4:]
    assert [s.applicable for s in seats] == [True, True, True, False]
    assert [f.applicable for f in fares] == [True, True, True, False]
    assert [s.transfer_count for s in seats] == [0, 1, 1, 0]


def test_evaluate_is_a_plain_coroutine_without_pool(store):
    # Stage-2 agents need no process pool: no A*, just lookups. They stay
    # async purely for a uniform gather() with the Stage-1 agents.
    assert inspect.iscoroutinefunction(SeatTransferAgent.evaluate)
    assert inspect.iscoroutinefunction(FareTimeAgent.evaluate)
    assert "pool" not in inspect.signature(SeatTransferAgent.__init__).parameters
    assert "pool" not in inspect.signature(FareTimeAgent.__init__).parameters


def test_two_stage_pipeline_end_to_end(store, heuristic, pool):
    """Stage 1 (search, in worker processes) then Stage 2 (score, in-process), all via gather."""
    q = UserQuery("PURI", "CDG", dt.date(2026, 9, 15), max_transfers=2)
    same, diff = SameTrainSearchAgent(store, heuristic, pool=pool), DifferentTrainSearchAgent(store, heuristic, pool=pool)
    seat, fare = SeatTransferAgent(store), FareTimeAgent(store)

    async def pipeline():
        proposals = await asyncio.gather(same.propose(q), diff.propose(q))          # stage 1
        scores = await asyncio.gather(                                              # stage 2
            *(seat.evaluate(p) for p in proposals), *(fare.evaluate(p) for p in proposals)
        )
        return proposals, scores

    (p_same, p_diff), (s_same, s_diff, f_same, f_diff) = asyncio.run(pipeline())
    assert not p_same.found and p_diff.found
    assert not s_same.applicable and not f_same.applicable
    assert s_diff.applicable and s_diff.transfer_count == 1 and s_diff.per_transfer_penalties[0]["kind"] == "different_train"
    assert f_diff.applicable and f_diff.layover_minutes > 400 and f_diff.total_fare > 0
    print(f"\nStage 1: {p_same.describe()}\n         {p_diff.describe()}\nStage 2: {s_diff.describe()}\n         {f_diff.describe()}")
