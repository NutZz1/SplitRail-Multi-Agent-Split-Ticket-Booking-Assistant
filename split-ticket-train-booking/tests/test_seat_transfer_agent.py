"""Tests for SeatTransferAgent on real proposals."""

from __future__ import annotations

import asyncio
import datetime as dt
from dataclasses import FrozenInstanceError

import pytest

from src.agents.seat_transfer_agent import (
    ASSUMED_COACH_DISTANCE_WHEN_UNKNOWN,
    COACH_DISTANCE_WEIGHT,
    DIFFERENT_TRAIN_TRANSFER_PENALTY,
    NIGHT_END_HOUR,
    NIGHT_START_HOUR,
    NIGHT_TRANSFER_PENALTY,
    SeatTransferAgent,
    TransferFeasibilityScore,
    is_night_hour,
)
from src.proposal import CandidateItinerary
from src.state import Ticket
from tests.proposal_fixtures import MAIL_QUERY, ride_with_transfer_at


@pytest.fixture(scope="module")
def agent(store):
    return SeatTransferAgent(store)


# --------------------------------------------------------------------------- #
# night window
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("hour,night", [(21, False), (22, True), (23, True), (0, True), (4, True), (5, False), (11, False)])
def test_is_night_hour(hour, night):
    assert (NIGHT_START_HOUR, NIGHT_END_HOUR) == (22, 5)
    assert is_night_hour(hour) is night


# --------------------------------------------------------------------------- #
# direct ride: nothing to score
# --------------------------------------------------------------------------- #
def test_direct_ride_has_no_transfers(agent, mail_proposal):
    score = asyncio.run(agent.evaluate(mail_proposal.best))
    assert isinstance(score, TransferFeasibilityScore)
    assert score.applicable and score.transfer_count == 0
    assert score.per_transfer_penalties == () and score.total_feasibility_penalty == 0.0


# --------------------------------------------------------------------------- #
# real same-train split at BNC (5-minute halt) -- also a real NIGHT transfer
# --------------------------------------------------------------------------- #
def test_bnc_same_train_transfer_scored(agent, store, bnc_split_proposal):
    score = asyncio.run(agent.evaluate(bnc_split_proposal.best))
    print("\n" + bnc_split_proposal.describe())
    print(score.describe())
    assert score.applicable and score.transfer_count == 1
    t = score.per_transfer_penalties[0]
    print("transfer detail:", t)
    assert t["station"] == "BNC" and t["kind"] == "same_train"
    assert t["train_from"] == t["train_to"] == "12658"
    assert t["buffer_minutes"] == 5.0   # the real BNC halt

    # coach distance from the real rake composition
    expected = store.coach_distance("12658", t["coach_from"], t["coach_to"])
    assert t["coach_distance"] == expected and expected is not None
    print(f"real coach_distance at BNC: {t['coach_from']} -> {t['coach_to']} = {expected} positions")
    if expected == 0:
        pytest.fail("a coach switch to the same coach is not a transfer")

    # 12658 departs BNC at 23:00 -> genuine night transfer from the real timetable
    assert bnc_split_proposal.best.tickets[1].boarding_datetime == dt.datetime(2026, 9, 16, 23, 0)
    assert t["is_night"] is True and t["boarding_time"] == "23:00"

    assert t["penalty"] == pytest.approx(expected * COACH_DISTANCE_WEIGHT + NIGHT_TRANSFER_PENALTY)
    assert t["penalty"] > 0
    assert score.total_feasibility_penalty == pytest.approx(t["penalty"])


@pytest.mark.parametrize("board_class", ["SL", "2A", "1A"])
def test_bnc_transfer_penalty_scales_with_real_coach_distance(agent, store, board_class):
    p = ride_with_transfer_at(store, MAIL_QUERY, "BNC", board_class=board_class)
    score = asyncio.run(agent.evaluate(p.best))
    t = score.per_transfer_penalties[0]
    d = store.coach_distance("12658", t["coach_from"], t["coach_to"])
    print(f"\nboarded {board_class}: switch {t['coach_from']} -> {t['coach_to']} = {d} positions, penalty {t['penalty']:.0f}")
    assert t["penalty"] == pytest.approx(d * COACH_DISTANCE_WEIGHT + NIGHT_TRANSFER_PENALTY)


# --------------------------------------------------------------------------- #
# real different-train transfer at NDLS (daytime, 11:30)
# --------------------------------------------------------------------------- #
def test_ndls_different_train_transfer_scored(agent, puri_cdg_proposal):
    score = asyncio.run(agent.evaluate(puri_cdg_proposal.best))
    print("\n" + puri_cdg_proposal.describe())
    print(score.describe())
    assert score.applicable and score.transfer_count == 1
    t = score.per_transfer_penalties[0]
    assert t["station"] == "NDLS" and t["kind"] == "different_train"
    assert (t["train_from"], t["train_to"]) == ("12801", "12217")
    assert t["coach_distance"] is None            # meaningless across two different rakes
    assert t["buffer_minutes"] == 400.0            # the real connection time
    assert t["boarding_time"] == "11:30" and t["is_night"] is False
    assert t["penalty"] == pytest.approx(DIFFERENT_TRAIN_TRANSFER_PENALTY)
    assert score.total_feasibility_penalty == pytest.approx(DIFFERENT_TRAIN_TRANSFER_PENALTY)


def test_real_night_transfer_exists_among_demo_trains(store, bnc_split_proposal, agent):
    """Documented answer to 'is there a genuine night transfer?': yes -- BNC on 12658 at 23:00.

    A cross-train one also exists in the timetable: 12609 arrives BNC 19:36 and
    12658 departs BNC 23:00 (buffer 204 min), a real night boarding."""
    score = asyncio.run(agent.evaluate(bnc_split_proposal.best))
    assert score.per_transfer_penalties[0]["is_night"]
    dep_12658 = next(s for s in store.get_real_halt_stops("12658") if s.station_code == "BNC").departure
    arr_12609 = next(s for s in store.get_real_halt_stops("12609") if s.station_code == "BNC").arrival
    print(f"\nreal night transfer: 12658 departs BNC {dep_12658}; also 12609 arr BNC {arr_12609} -> 12658 dep {dep_12658}")
    assert dep_12658 == "23:00:00" and is_night_hour(23)


# --------------------------------------------------------------------------- #
# missing composition data: never scored as adjacent
# --------------------------------------------------------------------------- #
def test_unknown_composition_is_penalised_not_zero(agent, mail_query):
    when = dt.datetime(2026, 9, 16, 12, 0)
    tickets = (
        Ticket("04601", "JAT", "SMVB", "S1", "SL", when, when + dt.timedelta(hours=1), "CONFIRMED"),
        Ticket("04601", "SMVB", "UHP", "S5", "SL", when + dt.timedelta(hours=1, minutes=5), when + dt.timedelta(hours=2), "CONFIRMED"),
    )
    candidate = CandidateItinerary("SameTrainSearchAgent", 0, None, tickets, 115.0, 1)  # type: ignore[arg-type]
    score = asyncio.run(agent.evaluate(candidate))
    t = score.per_transfer_penalties[0]
    assert t["coach_distance"] is None and t["note"] and "unavailable" in t["note"]
    assert t["is_night"] is False
    assert t["penalty"] == pytest.approx(ASSUMED_COACH_DISTANCE_WHEN_UNKNOWN * COACH_DISTANCE_WEIGHT)
    assert t["penalty"] > 0


# --------------------------------------------------------------------------- #
# not applicable
# --------------------------------------------------------------------------- #
def test_not_applicable_when_no_solution(agent, no_solution_proposal):
    assert no_solution_proposal.found is False
    # per-candidate scoring: a proposal with no candidates yields no scores at all
    assert asyncio.run(agent.evaluate_all(no_solution_proposal)) == ()
    score = TransferFeasibilityScore.not_applicable(no_solution_proposal.agent_name)
    assert score.applicable is False
    assert score.per_transfer_penalties == () and score.total_feasibility_penalty == 0.0
    assert score.proposal_agent_name == "SameTrainSearchAgent"
    assert "not applicable" in score.describe()


def test_score_is_frozen(agent, mail_proposal):
    score = asyncio.run(agent.evaluate(mail_proposal.best))
    with pytest.raises(FrozenInstanceError):
        score.applicable = False  # type: ignore[misc]


