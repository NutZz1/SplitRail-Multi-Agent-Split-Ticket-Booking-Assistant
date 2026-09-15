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
        return await asyncio.gather(seat.evaluate(bnc_split_proposal.best), fare.evaluate(bnc_split_proposal.best))

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
        return await asyncio.gather(*(seat.evaluate_all(p) for p in proposals), *(fare.evaluate_all(p) for p in proposals))

    results = asyncio.run(all_scores())
    seats, fares = results[:4], results[4:]
    # one score per candidate; the no-solution proposal has no candidates and so no scores
    assert [len(s) for s in seats] == [1, 1, 1, 0]
    assert [len(f) for f in fares] == [1, 1, 1, 0]
    assert [s[0].transfer_count for s in seats[:3]] == [0, 1, 1]
    assert all(sc.applicable for group in seats + fares for sc in group)


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
            *(seat.evaluate_all(p) for p in proposals), *(fare.evaluate_all(p) for p in proposals)
        )
        return proposals, scores

    (p_same, p_diff), (s_same, s_diff, f_same, f_diff) = asyncio.run(pipeline())
    assert not p_same.found and p_diff.found
    assert s_same == () and f_same == ()  # nothing to score
    assert len(s_diff) == len(f_diff) == len(p_diff.candidates)
    assert s_diff[0].transfer_count == 1 and s_diff[0].per_transfer_penalties[0]["kind"] == "different_train"
    assert f_diff[0].layover_minutes > 400 and f_diff[0].total_fare > 0
    print(f"\nStage 1: {p_same.describe()}\n         {p_diff.describe()}\nStage 2: {s_diff[0].describe()}\n         {f_diff[0].describe()}")
