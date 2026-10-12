"""
Ablation: src.baseline_single_agent vs CoordinatorAgent, on the same
standard demo queries used throughout tests/test_scenarios.py.

This is the test that backs docs/ablation.md. It asserts EQUIVALENCE (same
ranked itineraries, same scores) and only reports wall-clock for both: which
orchestration is faster is machine- and workload-dependent, so it is measured
by tools/benchmark_search.py rather than asserted here.
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


def test_baseline_and_coordinator_timings_are_reported_not_ranked(coordinator, store, heuristic):
    """Report both wall-clock totals; do NOT assert which is faster.

    This test used to assert the single-agent baseline was faster. That was
    never a stable property: docs/ablation.md now measures the coordinator as
    1.09-1.12x FASTER, this suite saw both outcomes on different machines,
    and the old loop always timed the baseline first so the coordinator
    inherited warm caches. Which orchestration wins depends on the machine and
    the workload, so a unit test cannot assert it either way.

    What IS stable -- and what the ablation actually relies on -- is that both
    return the same ranked answer; the parametrised test above asserts that.
    For a real measurement (warm-up, alternating order, medians) run
    ``tools/benchmark_search.py``.
    """
    base_total = multi_total = 0.0
    for i, (_, query) in enumerate(STANDARD_QUERIES):
        # Alternate who goes first so neither consistently gets warm caches.
        order = ("base", "multi") if i % 2 == 0 else ("multi", "base")
        for who in order:
            if who == "base":
                _, seconds = resolve_single_agent(query, store, heuristic)
                base_total += seconds
            else:
                t0 = time.perf_counter()
                asyncio.run(coordinator.resolve(query))
                multi_total += time.perf_counter() - t0
    print(f"\ntotals over {len(STANDARD_QUERIES)} queries: baseline {base_total * 1000:.1f} ms, "
          f"multi-agent {multi_total * 1000:.1f} ms")
    assert base_total > 0 and multi_total > 0
