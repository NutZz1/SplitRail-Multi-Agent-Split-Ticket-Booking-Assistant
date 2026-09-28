"""
Tests for src.trace and CoordinatorAgent's optional --trace coordination log.

Off by default (see coordinator_agent.py's ``resolve(query, trace=None)``);
these tests check the ON path records the right shape, in the right order,
and that turning it on never changes what resolve() actually returns.
"""

from __future__ import annotations

import asyncio
import datetime as dt

from src.agents.coordinator_agent import CoordinatorAgent
from src.agents.different_train_search_agent import DifferentTrainSearchAgent
from src.agents.fare_time_agent import FareTimeAgent
from src.agents.same_train_search_agent import SameTrainSearchAgent
from src.agents.seat_transfer_agent import SeatTransferAgent
from src.state import UserQuery
from src.trace import TraceRecorder

WED = dt.date(2026, 9, 16)   # 12658 runs daily: direct + BNC split, a known two-candidate query
SBC_MAS = UserQuery("SBC", "MAS", WED, max_transfers=2)


def coordinator(store, heuristic):
    return CoordinatorAgent(
        SameTrainSearchAgent(store, heuristic), DifferentTrainSearchAgent(store, heuristic),
        SeatTransferAgent(store), FareTimeAgent(store), store=store,
    )


def test_trace_records_expected_event_kinds_in_a_valid_order(store, heuristic):
    coord = coordinator(store, heuristic)
    recorder = TraceRecorder()
    rec = asyncio.run(coord.resolve(SBC_MAS, trace=recorder))
    assert rec.found
    kinds = [e.kind for e in recorder.events]

    # sequence numbers are monotonic 1..N and offsets never go backwards
    assert [e.seq for e in recorder.events] == list(range(1, len(recorder.events) + 1))
    offsets = [e.offset_ms for e in recorder.events]
    assert offsets == sorted(offsets)

    assert kinds[0] == "task_announced"
    assert kinds.count("proposal_returned") == 2               # SameTrain + DifferentTrain
    assert kinds.count("duplicates_merged") == 1
    assert kinds.count("hard_constraint_prunes") == 1
    assert kinds.count("stage2_evaluation_completed") == 2 * len(rec.ranked_options)  # seat + fare, per candidate
    assert kinds.count("pareto_front_computed") == 1
    assert kinds[-1] == "final_ranking"

    # task announced -> both proposals returned -> merged -> pruned -> every
    # Stage 2 evaluation completed -> Pareto front -> final ranking
    i_proposals = [i for i, k in enumerate(kinds) if k == "proposal_returned"]
    i_stage2 = [i for i, k in enumerate(kinds) if k == "stage2_evaluation_completed"]
    assert 0 < min(i_proposals)
    assert max(i_proposals) < kinds.index("duplicates_merged") < kinds.index("hard_constraint_prunes")
    assert kinds.index("hard_constraint_prunes") < min(i_stage2)
    assert max(i_stage2) < kinds.index("pareto_front_computed") < kinds.index("final_ranking")


def test_trace_never_changes_resolves_result(store, heuristic):
    """Turning tracing on must not change the recommendation itself, byte-identical."""
    coord = coordinator(store, heuristic)
    rec_off = asyncio.run(coord.resolve(SBC_MAS))
    recorder = TraceRecorder()
    rec_on = asyncio.run(coord.resolve(SBC_MAS, trace=recorder))
    assert recorder.events               # tracing genuinely recorded something
    assert rec_off == rec_on


def test_trace_table_is_readable_and_ordered(store, heuristic):
    coord = coordinator(store, heuristic)
    recorder = TraceRecorder()
    asyncio.run(coord.resolve(SBC_MAS, trace=recorder))
    table = recorder.table()
    lines = table.splitlines()
    assert "#" in lines[0] and "event" in lines[0]             # header
    assert len(lines) == len(recorder.events) + 2              # header + separator + one row per event
    assert "task_announced" in table and "final_ranking" in table
