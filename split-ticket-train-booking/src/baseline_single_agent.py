"""
Single-agent, single-process baseline for the ablation in docs/ablation.md.

CoordinatorAgent (src.agents.coordinator_agent) runs two Stage-1 searches
concurrently via asyncio.gather(), then two Stage-2 evaluations per
candidate concurrently via another asyncio.gather(). This module answers
the ablation's question -- what does that decomposition actually buy, and
what does it cost -- by running the exact same steps in ONE control loop,
ONE process, with no asyncio-based concurrency at all: both searches run
one after the other, and every Stage-2 evaluation runs one after the
other.

Reused, not reimplemented:
    astar_search_k, get_successors, get_successors_different_train
        -- the identical search code CoordinatorAgent's Stage-1 agents call
    same_train_search_agent.diagnose_failure,
    different_train_search_agent.diagnose_failure
        -- the identical failure-explanation code
    ItineraryProposal.from_search
        -- the identical packaging of a search result
    same_train_query, dedupe_across_agents, passes_hard_constraints,
    rank_candidates (src.agents.coordinator_agent)
        -- the identical capping, de-duplication, pruning and ranking logic
    SeatTransferAgent.evaluate, FareTimeAgent.evaluate
        -- the identical Stage-2 scoring code

The one unavoidable use of asyncio: SeatTransferAgent.evaluate() and
FareTimeAgent.evaluate() are ``async def`` (only so CoordinatorAgent can
gather() them uniformly -- see their module docstrings; neither does any
real I/O). There is no synchronous entry point to call instead, so this
baseline drives each one to completion with a single ``asyncio.run()``
call per candidate, strictly one at a time, never gathered -- the minimal
shim needed to reuse that code without a second implementation of it.
"""

from __future__ import annotations

import asyncio
import time

from src.agents.coordinator_agent import (
    dedupe_across_agents,
    passes_hard_constraints,
    rank_candidates,
    same_train_query,
)
from src.agents.different_train_search_agent import K_CANDIDATES as DIFFERENT_TRAIN_K_CANDIDATES
from src.agents.different_train_search_agent import diagnose_failure as diagnose_different_train_failure
from src.agents.fare_time_agent import FareTimeAgent
from src.agents.same_train_search_agent import K_CANDIDATES as SAME_TRAIN_K_CANDIDATES
from src.agents.same_train_search_agent import diagnose_failure as diagnose_same_train_failure
from src.agents.seat_transfer_agent import SeatTransferAgent
from src.data_store import RailDataStore
from src.final_recommendation import FinalRecommendation, SearchEffort
from src.heuristic import RailHeuristic
from src.proposal import CandidateItinerary, ItineraryProposal
from src.seat_search import search_seats
from src.state import UserQuery


def resolve_single_agent(
    query: UserQuery, store: RailDataStore, heuristic: RailHeuristic,
) -> tuple[FinalRecommendation, float]:
    """CoordinatorAgent.resolve(), but sequential and synchronous. Returns
    ``(recommendation, wall_clock_seconds)``, the same shape as
    ``src.demo_scenarios.resolve_timed``."""
    t0 = time.perf_counter()

    # ---- Stage 1: same-train, THEN different-train (not gathered) --------
    same_goals, same_stats = search_seats(same_train_query(query), store, heuristic, "same_train", k=SAME_TRAIN_K_CANDIDATES)
    same_reason = None if same_goals else diagnose_same_train_failure(query, store, same_stats)
    same_p = ItineraryProposal.from_search("SameTrainSearchAgent", query, same_goals, same_stats, same_reason)

    diff_goals, diff_stats = search_seats(query, store, heuristic, "different_train", k=DIFFERENT_TRAIN_K_CANDIDATES)
    diff_reason = None if diff_goals else diagnose_different_train_failure(query, store, diff_stats)
    diff_p = ItineraryProposal.from_search("DifferentTrainSearchAgent", query, diff_goals, diff_stats, diff_reason)

    effort = (
        SearchEffort(same_p.agent_name, same_p.search_stats.nodes_expanded, same_p.found),
        SearchEffort(diff_p.agent_name, diff_p.search_stats.nodes_expanded, diff_p.found),
    )

    if not same_p.found and not diff_p.found:
        rec = FinalRecommendation(
            query=query,
            found=False,
            failure_reason=f"{same_p.agent_name}: {same_p.failure_reason} {diff_p.agent_name}: {diff_p.failure_reason}",
            ranked_options=(),
            total_candidates_considered=0,
            candidates_pruned_by_hard_constraint=0,
            search_effort_summary=effort,
        )
        return rec, time.perf_counter() - t0

    pooled: list[CandidateItinerary] = [*same_p.candidates, *diff_p.candidates]
    total = len(pooled)
    pooled = dedupe_across_agents(pooled)
    merged = total - len(pooled)

    survivors = [c for c in pooled if passes_hard_constraints(c, query, store)]
    pruned = len(pooled) - len(survivors)
    if not survivors:
        rec = FinalRecommendation(
            query=query,
            found=False,
            failure_reason=f"All {total} candidate(s) failed the hard-constraint re-check.",
            ranked_options=(),
            total_candidates_considered=total,
            candidates_pruned_by_hard_constraint=pruned,
            duplicate_candidates_merged=merged,
            search_effort_summary=effort,
        )
        return rec, time.perf_counter() - t0

    # ---- Stage 2: every evaluate(), one candidate at a time ---------------
    seat, fare = SeatTransferAgent(store), FareTimeAgent(store)
    transfer_scores = [asyncio.run(seat.evaluate(c)) for c in survivors]
    fare_scores = [asyncio.run(fare.evaluate(c)) for c in survivors]

    ranked = rank_candidates(survivors, transfer_scores, fare_scores)
    rec = FinalRecommendation(
        query=query,
        found=True,
        failure_reason=None,
        ranked_options=tuple(ranked),
        total_candidates_considered=total,
        candidates_pruned_by_hard_constraint=pruned,
        duplicate_candidates_merged=merged,
        search_effort_summary=effort,
    )
    return rec, time.perf_counter() - t0
