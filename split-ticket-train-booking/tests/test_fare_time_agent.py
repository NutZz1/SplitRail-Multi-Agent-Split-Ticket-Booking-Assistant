"""Tests for FareTimeAgent on real proposals."""

from __future__ import annotations

import asyncio

import pytest

from src.agents.fare_time_agent import FareTimeAgent, FareTimeScore
from src.fare_model import FARE_PER_KM, compute_total_fare
from src.distance import route_distance_km


@pytest.fixture(scope="module")
def agent(store):
    return FareTimeAgent(store)


def test_sbc_mas_direct_numbers(agent, store, mail_proposal):
    score = asyncio.run(agent.evaluate(mail_proposal))
    print("\n" + mail_proposal.describe())
    print(score.describe())
    assert isinstance(score, FareTimeScore) and score.applicable
    assert score.moving_time_minutes == 340                      # g(n), carried through
    assert score.wall_clock_minutes == 355                       # 22:45 -> 04:40 next day, from the timetable
    assert score.wall_clock_minutes >= score.moving_time_minutes
    assert score.layover_minutes == 15                           # dwell at the 6 intermediate halts
    # fare: class of the single ticket x real route distance
    cls = mail_proposal.tickets[0].travel_class
    km = route_distance_km("12658", "SBC", "MAS", store)
    assert score.total_fare == pytest.approx(km * FARE_PER_KM[cls], abs=0.01)
    assert 100 < score.total_fare < 2000
    assert score.per_ticket_fares == (score.total_fare,)


def test_wall_clock_is_independent_of_g(agent, mail_proposal):
    # wall-clock is recomputed from ticket datetimes, not derived from total_time_minutes
    score = asyncio.run(agent.evaluate(mail_proposal))
    t = mail_proposal.tickets
    assert score.wall_clock_minutes == (t[-1].alighting_datetime - t[0].boarding_datetime).total_seconds() / 60


def test_bnc_split_has_positive_layover(agent, bnc_split_proposal):
    score = asyncio.run(agent.evaluate(bnc_split_proposal))
    print("\n" + bnc_split_proposal.describe())
    print(score.describe(), "| per-ticket", score.per_ticket_fares)
    assert bnc_split_proposal.transfer_count == 1
    assert score.layover_minutes > 0
    assert score.wall_clock_minutes > score.moving_time_minutes   # the dimension g(n) cannot see
    assert score.total_fare == pytest.approx(sum(score.per_ticket_fares), abs=0.01)
    assert len(score.per_ticket_fares) == 2


def test_puri_cdg_layover_is_the_real_connection_plus_dwell(agent, puri_cdg_proposal):
    score = asyncio.run(agent.evaluate(puri_cdg_proposal))
    print("\n" + puri_cdg_proposal.describe())
    print(score.describe(), "| per-ticket", score.per_ticket_fares)
    assert score.moving_time_minutes == puri_cdg_proposal.total_time_minutes
    t = puri_cdg_proposal.tickets
    wall = (t[-1].alighting_datetime - t[0].boarding_datetime).total_seconds() / 60
    assert score.wall_clock_minutes == wall
    assert score.layover_minutes >= 400          # at least the real NDLS connection
    assert score.layover_minutes > 400           # plus dwell along 12801's 26 halts and 12217's
    assert score.total_fare is not None and score.total_fare > 0
    # the long leg costs more than the short one
    assert score.per_ticket_fares[0] > score.per_ticket_fares[1]


def test_split_fare_differs_from_direct_when_class_changes(agent, mail_proposal, bnc_split_proposal):
    direct = asyncio.run(agent.evaluate(mail_proposal))
    split = asyncio.run(agent.evaluate(bnc_split_proposal))
    classes = [t.travel_class for t in bnc_split_proposal.tickets]
    if len(set(classes)) > 1 or classes[0] != mail_proposal.tickets[0].travel_class:
        assert split.total_fare != direct.total_fare
    assert split.moving_time_minutes == direct.moving_time_minutes == 340


def test_not_applicable_when_no_solution(agent, no_solution_proposal):
    score = asyncio.run(agent.evaluate(no_solution_proposal))
    assert score.applicable is False
    assert score.total_fare is None
    assert score.moving_time_minutes == score.wall_clock_minutes == score.layover_minutes == 0.0
    assert score.per_ticket_fares == ()
    assert "not applicable" in score.describe()


def test_score_is_frozen(agent, mail_proposal):
    score = asyncio.run(agent.evaluate(mail_proposal))
    with pytest.raises(Exception):
        score.total_fare = 0.0  # type: ignore[misc]


def test_total_fare_matches_fare_model(agent, store, bnc_split_proposal):
    score = asyncio.run(agent.evaluate(bnc_split_proposal))
    assert score.total_fare == compute_total_fare(bnc_split_proposal.tickets, store)
