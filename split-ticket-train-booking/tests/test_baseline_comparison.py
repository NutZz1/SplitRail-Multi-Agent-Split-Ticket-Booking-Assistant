"""
Ablation: src.baseline_single_agent vs CoordinatorAgent, on the same
standard demo queries used throughout tests/test_scenarios.py.

This is the test that backs docs/ablation.md's numbers. It asserts
equivalence (same top-ranked itinerary) and reports wall-clock for both --
the baseline is expected to be FASTER, which is the honest result and the
point of the ablation, not a regression to chase away.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import time

import pytest

from src.agents.coordinator_agent import CoordinatorAgent
from src.agents.different_train_search_agent import DifferentTrainSearchAgent
from src.agents.fare_time_agent import FareTimeAgent
from src.agents.same_train_search_agent import SameTrainSearchAgent
from src.agents.seat_transfer_agent import SeatTransferAgent
from src.baseline_single_agent import resolve_single_agent
from src.state import UserQuery

WED = dt.date(2026, 9, 16)   # 12658 runs, 12217 does not
TUE = dt.date(2026, 9, 15)   # 12801 and 12217 both run

#: The same four standard scenarios as src.demo_scenarios.demo_scenarios(),
#: with concrete pinned dates (see tests/test_scenarios.py's WED/TUE) rather
#: than the rolling window, so the ablation is reproducible independent of
#: today's date.
STANDARD_QUERIES = (
    ("SBC -> MAS direct+split", UserQuery("SBC", "MAS", WED, max_transfers=2)),
    ("PURI -> CDG connection", UserQuery("PURI", "CDG", TUE, max_transfers=2)),
    ("SBC -> MAS hard CC (no itinerary)",
     UserQuery("SBC", "MAS", WED, travel_class_preference="CC", class_is_hard_constraint=True)),
    ("PURI -> CDG on a Wednesday (12217 does not run)", UserQuery("PURI", "CDG", WED, max_transfers=2)),
)


@pytest.fixture(scope="module")
def coordinator(store, heuristic):
    return CoordinatorAgent(
        SameTrainSearchAgent(store, heuristic), DifferentTrainSearchAgent(store, heuristic),
        SeatTransferAgent(store), FareTimeAgent(store), store=store,
    )


@pytest.mark.parametrize("label,query", STANDARD_QUERIES, ids=[q[0] for q in STANDARD_QUERIES])
def test_baseline_matches_coordinator_top_ranked_itinerary(coordinator, store, heuristic, label, query):
    t0 = time.perf_counter()
    rec_multi = asyncio.run(coordinator.resolve(query))
    multi_seconds = time.perf_counter() - t0

    rec_base, base_seconds = resolve_single_agent(query, store, heuristic)

    print(f"\n{label}: baseline {base_seconds * 1000:6.1f} ms  |  multi-agent {multi_seconds * 1000:6.1f} ms "
          f"(baseline {'faster' if base_seconds < multi_seconds else 'SLOWER'} by "
          f"{abs(multi_seconds - base_seconds) * 1000:.1f} ms)")

    assert rec_base.found == rec_multi.found, label
    if not rec_multi.found:
        return
    assert rec_base.best is not None and rec_multi.best is not None
    assert rec_base.best.candidate.signature == rec_multi.best.candidate.signature, label
    assert rec_base.best.final_score == pytest.approx(rec_multi.best.final_score), label
    # every candidate the multi-agent coordinator surfaced, the baseline surfaces too (same search, same ranking)
    assert {o.candidate.signature for o in rec_base.ranked_options} == \
           {o.candidate.signature for o in rec_multi.ranked_options}, label


def test_baseline_is_faster_than_the_multi_agent_coordinator_on_average(coordinator, store, heuristic):
    """The honest ablation result: one process avoiding asyncio.gather(),
    worker-process, and event-loop overhead is faster than the multi-agent
    path on these small, single-machine demo queries. See docs/ablation.md
    for where the multi-agent architecture wins instead (graceful
    degradation under a Task 4-style failure, and surfacing the fare-vs-risk
    trade-off via the Pareto front)."""
    found_queries = [q for _, q in STANDARD_QUERIES]
    base_total = 0.0
    multi_total = 0.0
    for query in found_queries:
        _, base_seconds = resolve_single_agent(query, store, heuristic)
        t0 = time.perf_counter()
        asyncio.run(coordinator.resolve(query))
        multi_total += time.perf_counter() - t0
        base_total += base_seconds
    print(f"\ntotals over {len(found_queries)} queries: baseline {base_total * 1000:.1f} ms, "
          f"multi-agent {multi_total * 1000:.1f} ms")
    assert base_total < multi_total
