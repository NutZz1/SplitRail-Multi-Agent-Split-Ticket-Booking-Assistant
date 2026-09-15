"""
Formal scenario suite -- the system's behaviour on the situations the design
identified as important, on REAL data, end to end through CoordinatorAgent.

Run with ``pytest -s tests/test_scenarios.py`` to see one human-readable
summary per scenario; this file doubles as review documentation.

Trains and dates used (all real timetable data; availability and run-dates
are the package's synthetic layer over the 6 demo trains):

    12658  Bangalore-Chennai Mail        SBC -> MAS   daily      halts BNC(5m) BWT JTJ KPD AJJ PER
    12801  Purushottam Express           PURI -> NDLS daily
    12217  Kerala Sampark Kranti         KCVL -> CDG  Tue/Fri    connects with 12801 at NDLS (400 min)
    04601  Jammu Tawi - Udhampur special JAT -> UHP   (real train, NO availability / run-date data)
"""

from __future__ import annotations

import asyncio
import datetime as dt

import pytest

from src.agents.coordinator_agent import (
    W_FARE,
    W_LAYOVER,
    W_TIME,
    W_TRANSFER,
    CoordinatorAgent,
)
from src.agents.different_train_search_agent import DifferentTrainSearchAgent
from src.agents.fare_time_agent import FareTimeAgent
from src.agents.same_train_search_agent import SameTrainSearchAgent
from src.agents.seat_transfer_agent import SeatTransferAgent
from src.state import UserQuery

WED = dt.date(2026, 9, 16)   # 12658 runs, 12217 does not
TUE = dt.date(2026, 9, 15)   # 12801 and 12217 both run


def summary(title: str, rec, extra: str = "") -> None:
    print(f"\n>>> {title}")
    if rec.found:
        print(f"    found: {len(rec.ranked_options)} option(s); winner: {rec.best.candidate.agent_name} "
              f"{' | '.join(t.describe() for t in rec.best.candidate.tickets)}  score {rec.best.final_score:.1f}")
    else:
        print(f"    found: NO -- {rec.failure_reason}")
    print("    effort: " + "; ".join(e.describe() for e in rec.search_effort_summary))
    if extra:
        print(f"    {extra}")


@pytest.fixture(scope="module")
def agents(store, heuristic):
    return (SameTrainSearchAgent(store, heuristic), DifferentTrainSearchAgent(store, heuristic),
            SeatTransferAgent(store), FareTimeAgent(store))


@pytest.fixture(scope="module")
def coordinator(agents, store):
    return CoordinatorAgent(*agents, store=store)


def resolve(coordinator, query):
    return asyncio.run(coordinator.resolve(query))


# =========================================================================== #
class TestScenario1_HappyPath_DirectBeatsSplitOnMerit:
    def test_direct_and_split_both_surface_and_direct_wins_because_of_the_transfer_penalty(self, coordinator):
        """SBC -> MAS on 12658: both the direct ride and the BNC coach-switch split are
        found (they tie on moving time, 340 min); the direct ride is ranked #1 with a
        lower score, and the entire gap is explained by the split's transfer penalty
        (11-position coach walk at BNC, boarding at 23:00 = night) minus its small
        fare saving."""
        rec = resolve(coordinator, UserQuery("SBC", "MAS", WED, max_transfers=2))
        assert rec.found
        by_t = {o.candidate.transfer_count: o for o in rec.ranked_options}
        assert set(by_t) == {0, 1}
        direct, split = by_t[0], by_t[1]
        assert rec.best is direct and direct.final_score < split.final_score
        assert direct.transfer_score.total_feasibility_penalty == 0
        assert split.transfer_score.total_feasibility_penalty > 0
        assert split.candidate.split_stations == ("BNC",)
        # the score gap is exactly the transfer term plus the fare difference
        gap = split.final_score - direct.final_score
        expected_gap = (W_TRANSFER * split.transfer_score.total_feasibility_penalty
                        + W_FARE * (split.fare_time_score.total_fare - direct.fare_time_score.total_fare))
        assert gap == pytest.approx(expected_gap, abs=1e-6)
        summary("1. direct beats split on merit", rec,
                f"direct {direct.final_score:.1f} vs split {split.final_score:.1f}; gap {gap:.1f} = "
                f"{W_TRANSFER}x penalty {split.transfer_score.total_feasibility_penalty:.0f} "
                f"+ fare diff {split.fare_time_score.total_fare - direct.fare_time_score.total_fare:+.2f}")


# =========================================================================== #
class TestScenario2_MultiTrainConnectionSucceedsWhereSameTrainCannot:
    def test_agents_cover_different_parts_of_the_space(self, agents, coordinator):
        """PURI -> CDG: no single train covers the route, so SameTrainSearchAgent
        correctly returns found=False, while DifferentTrainSearchAgent finds the real
        12801 -> 12217 connection at NDLS (400-minute buffer). The coordinator
        surfaces it as the recommendation: the two Stage-1 agents search different
        parts of the space and the architecture needs both."""
        same, diff, _, _ = agents
        q = UserQuery("PURI", "CDG", TUE, max_transfers=2)
        p_same = asyncio.run(same.propose(q))
        p_diff = asyncio.run(diff.propose(q))
        assert not p_same.found and "No single train runs PURI -> CDG" in p_same.failure_reason
        assert p_diff.found
        assert p_diff.best.signature == (("12801", "PURI", "NDLS"), ("12217", "NDLS", "CDG"))
        rec = resolve(coordinator, q)
        assert rec.found and rec.best.candidate.agent_name == "DifferentTrainSearchAgent"
        assert rec.best.candidate.signature == p_diff.best.signature
        assert rec.best.transfer_score.per_transfer_penalties[0]["buffer_minutes"] == 400
        summary("2. multi-train connection where same-train cannot", rec,
                f"same-train: {p_same.failure_reason}")


# =========================================================================== #
class TestScenario3_HardConstraint_WrongRunDate:
    def test_wrong_weekday_for_12217_is_an_explained_failure(self, coordinator, store):
        """KCVL -> CDG on Wednesday 2026-09-16: 12217 is the only covered train from
        KCVL and it runs Tue/Fri only. No candidate can exist, so found=False is the
        correct answer -- and the failure_reason names the run-date cause with the
        actual pattern, not a generic message."""
        assert not store.runs_on_date("12217", WED.isoformat())
        rec = resolve(coordinator, UserQuery("KCVL", "CDG", WED, max_transfers=2))
        assert not rec.found and rec.ranked_options == ()
        assert "runs on 2026-09-16 (WED)" in rec.failure_reason
        assert "12217 runs TUE/FRI" in rec.failure_reason
        assert "coverage" not in rec.failure_reason      # it is a wrong-day case, not a coverage case
        summary("3. wrong run-date is an explained failure", rec)

    def test_wrong_weekday_removes_only_that_trains_candidates_elsewhere(self, coordinator):
        """PURI -> CDG on the same Wednesday: 12801 (daily) still runs and is searched
        to its end, but its only onward connection (12217) does not run, so the
        result is found=False with 12801's search effort visible."""
        rec = resolve(coordinator, UserQuery("PURI", "CDG", WED, max_transfers=2))
        assert not rec.found
        assert "no connecting train (running 2026-09-16" in rec.failure_reason
        effort = {e.agent_name: e.nodes_expanded for e in rec.search_effort_summary}
        assert effort["DifferentTrainSearchAgent"] > 50        # rode 12801 all the way to NDLS
        summary("3b. wrong run-date on the connecting train", rec)


# =========================================================================== #
class TestScenario4_HardConstraint_ClassExclusion:
    def test_hard_cc_on_a_route_with_no_chair_car(self, coordinator):
        """SBC -> MAS with class_is_hard_constraint=True and class CC: 12658 has no
        chair car. found=False, the reason names the class, and the search-effort
        summary (Part A) shows both agents ran their search (each expanded the start
        state, found nothing bookable in CC, and stopped -- the correct effort for a
        boarding-time exclusion)."""
        rec = resolve(coordinator, UserQuery("SBC", "MAS", WED, travel_class_preference="CC", class_is_hard_constraint=True))
        assert not rec.found
        assert "CC" in rec.failure_reason and "not offered" in rec.failure_reason
        effort = {e.agent_name: e for e in rec.search_effort_summary}
        assert set(effort) == {"SameTrainSearchAgent", "DifferentTrainSearchAgent"}
        assert all(e.nodes_expanded >= 1 and not e.found for e in effort.values())
        summary("4. hard class exclusion is an explained failure", rec)

    def test_hard_class_that_exists_is_honoured(self, coordinator):
        """Control: a hard 3A requirement on the same route succeeds and every leg of
        every option is 3A."""
        rec = resolve(coordinator, UserQuery("SBC", "MAS", WED, travel_class_preference="3A", class_is_hard_constraint=True))
        assert rec.found
        assert all(t.travel_class == "3A" for o in rec.ranked_options for t in o.candidate.tickets)
        summary("4b. hard class that exists is honoured", rec)


# =========================================================================== #
class TestScenario5_TotalFailureShortCircuit:
    def test_stage_2_never_runs_when_both_searches_fail(self, agents, store):
        """Both Stage-1 agents fail -> the coordinator returns immediately; neither
        SeatTransferAgent.evaluate nor FareTimeAgent.evaluate is ever called
        (call counts asserted, not inferred from the empty result)."""
        same, diff, seat, fare = agents
        calls = {"seat": 0, "fare": 0}

        class Spy:
            def __init__(self, inner, key):
                self.inner, self.key = inner, key

            async def evaluate(self, c):
                calls[self.key] += 1
                return await self.inner.evaluate(c)

        coord = CoordinatorAgent(same, diff, Spy(seat, "seat"), Spy(fare, "fare"), store=store)  # type: ignore[arg-type]
        rec = resolve(coord, UserQuery("SBC", "MAS", WED, travel_class_preference="CC", class_is_hard_constraint=True))
        assert not rec.found and calls == {"seat": 0, "fare": 0}
        assert len(rec.search_effort_summary) == 2
        summary("5. total-failure short-circuit", rec, f"Stage 2 evaluate() calls: {calls}")


# =========================================================================== #
class TestScenario6_RacWaitlistSurfacesButIsNotScored:
    def test_risky_candidate_is_ranked_and_score_ignores_seat_status(self, coordinator, store):
        """PURI -> CDG's recommended itinerary uses real synthetic-availability statuses
        RAC (12801 PURI->NDLS, coach S1) and WAITLIST (12217 NDLS->CDG, coach S1).
        By design RAC/WAITLIST are soft risk, not hard exclusion, so the candidate
        appears in ranked_options with the statuses on its tickets.

        FINDING: final_score does NOT account for seat status. The score is exactly
        W_TIME*moving + W_FARE*fare + W_TRANSFER*penalty + W_LAYOVER*layover and no
        term reads Ticket.status; neither FareTimeAgent nor SeatTransferAgent uses
        it. Ticket.is_risky / CandidateItinerary.is_risky exist and are surfaced,
        but the design's 'risk_penalty' was never implemented. This test documents
        that gap rather than hiding it."""
        rec = resolve(coordinator, UserQuery("PURI", "CDG", TUE, max_transfers=2))
        assert rec.found
        best = rec.best
        statuses = [(t.train_number, t.from_station, t.to_station, t.coach, t.status) for t in best.candidate.tickets]
        assert best.candidate.is_risky
        assert {t.status for t in best.candidate.tickets} & {"RAC", "WAITLIST"}
        # real statuses straight from seat_availability
        for t in best.candidate.tickets:
            assert store.get_availability(t.train_number, t.from_station, t.to_station)[t.coach] == t.status
        # the score has no risk term: it is fully explained by the four weighted components
        f, s = best.fare_time_score, best.transfer_score
        assert best.final_score == pytest.approx(
            W_TIME * f.moving_time_minutes + W_FARE * f.total_fare
            + W_TRANSFER * s.total_feasibility_penalty + W_LAYOVER * f.layover_minutes)
        summary("6. RAC/WAITLIST surfaces but is not scored", rec,
                f"statuses used: {statuses}  -- final_score contains NO risk term (known gap)")


# =========================================================================== #
class TestScenario7_TransferLimitRespected:
    def test_max_transfers_zero_yields_only_direct_options(self, coordinator):
        """max_transfers=0: every option has 0 transfers. The coordinator's own
        same-train cap (max_transfers=1) must not raise the user's stricter 0 --
        the split at BNC that exists at max_transfers>=1 must be absent."""
        rec = resolve(coordinator, UserQuery("SBC", "MAS", WED, max_transfers=0))
        assert rec.found
        assert all(o.candidate.transfer_count == 0 for o in rec.ranked_options)
        assert len(rec.ranked_options) == 1        # only the direct ride
        with_one = resolve(coordinator, UserQuery("SBC", "MAS", WED, max_transfers=1))
        assert any(o.candidate.transfer_count == 1 for o in with_one.ranked_options)
        summary("7. transfer limit respected", rec, "max_transfers=0 -> direct only; =1 -> BNC split appears")

    def test_max_transfers_zero_makes_a_connection_query_fail_cleanly(self, coordinator):
        """PURI -> CDG needs one train change; with max_transfers=0 the honest answer
        is found=False, and the reason says so."""
        rec = resolve(coordinator, UserQuery("PURI", "CDG", TUE, max_transfers=0))
        assert not rec.found and "max_transfers=0" in rec.failure_reason
        summary("7b. connection impossible under max_transfers=0", rec)


# =========================================================================== #
class TestScenario8_PassengerCount_CurrentBehaviour:
    def test_passenger_count_is_carried_but_not_enforced(self, coordinator):
        """CURRENT BEHAVIOUR, documented honestly: passenger_count is accepted by
        UserQuery, copied onto every JourneyState, and otherwise unused -- it does
        not affect availability checks, candidates, or scores. Seat-adjacency /
        group modelling was scoped out of this project (single-passenger-equivalent
        seat check only), so a group query returns exactly the single-passenger
        result. This test asserts that equivalence; it deliberately does not make
        passenger_count start doing anything."""
        one = resolve(coordinator, UserQuery("SBC", "MAS", WED, max_transfers=2, passenger_count=1))
        four = resolve(coordinator, UserQuery("SBC", "MAS", WED, max_transfers=2, passenger_count=4))
        assert one.found and four.found
        assert [o.candidate.signature for o in one.ranked_options] == [o.candidate.signature for o in four.ranked_options]
        assert [o.final_score for o in one.ranked_options] == [o.final_score for o in four.ranked_options]
        assert [o.fare_time_score.total_fare for o in one.ranked_options] == [o.fare_time_score.total_fare for o in four.ranked_options]
        assert all(o.candidate.goal_state.passenger_count == 4 for o in four.ranked_options)  # carried through
        summary("8. passenger_count is carried but not enforced", four,
                "identical options, scores and fares for 1 and 4 passengers (per-passenger fare/adjacency out of scope)")


# =========================================================================== #
class TestScenario9_DemoSubsetCoverageIsExplicit:
    def test_uncovered_route_fails_with_a_coverage_message(self, coordinator, store):
        """JAT -> UHP on 2026-09-16: served in the real timetable (e.g. 04601) but by
        no train with availability / run-date data. The result must be found=False
        AND say so as a DATA-COVERAGE limit, naming the covered trains -- not as a
        search failure and not as 'these trains don't run today'."""
        assert store.get_all_trains_through_station("JAT")           # real trains exist here
        assert not any(store.get_run_pattern(t) for t in store.get_all_trains_through_station("JAT"))
        rec = resolve(coordinator, UserQuery("JAT", "UHP", WED, max_transfers=2))
        assert not rec.found
        assert "data-coverage limit, not a search failure" in rec.failure_reason
        assert "12658" in rec.failure_reason                          # the covered set is listed
        assert "runs on" not in rec.failure_reason                    # not mis-described as a wrong-day case
        summary("9. demo-subset coverage is explicit", rec)
