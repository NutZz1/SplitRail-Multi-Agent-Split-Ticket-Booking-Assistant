"""
The BFS / UCS / A* comparison shown in "How SplitRail decided".

These tests assert textbook PROPERTIES, not particular numbers, so they keep
holding when the timetable, the seat seed or the weights change -- which is
the point: the interface renders whatever the engine measures, and these
tests are what make that measurement trustworthy.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from src.explain import AGENT_FOR_MODE, compare_algorithms, mode_for_journey_type
from src.state import UserQuery

# PGT -> KYJ in the same-train space is the canonical teaching query in the
# test profile: BFS settles for a dearer journey with fewer tickets, UCS and A*
# both find the optimum, and A* expands far fewer nodes doing it. (The app's
# kerala-v1 profile has its own examples -- docs/presentation-guide.md.)
ORIGIN, DESTINATION, MODE = "PGT", "KYJ", "same_train"


@pytest.fixture(scope="module")
def comparison(store, heuristic):
    """The first date in the pinned window on which the query has a route."""
    lo, hi = store.get_run_date_range()
    day = dt.date.fromisoformat(lo)
    while day.isoformat() <= hi:
        query = UserQuery(ORIGIN, DESTINATION, day, max_transfers=1)
        result = compare_algorithms(store, heuristic, query, MODE)
        if all(r["found"] and not r["truncated"] for r in result["runs"]):
            return result
        day += dt.timedelta(days=1)
    pytest.skip(f"{ORIGIN}->{DESTINATION} has no complete comparison in the pinned window")


def runs_by_name(result):
    return {r["algorithm"]: r for r in result["runs"]}


# --------------------------------------------------------------------------- #
# Shape
# --------------------------------------------------------------------------- #
def test_three_algorithms_in_teaching_order(comparison):
    assert [r["algorithm"] for r in comparison["runs"]] == ["BFS", "UCS", "A*"]
    assert comparison["agent"] == AGENT_FOR_MODE[MODE]


def test_result_is_strict_json(comparison):
    """No NaN/Infinity: an unreachable station's h must not break the page."""
    json.dumps(comparison, allow_nan=False)


def test_only_bfs_lacks_an_optimality_guarantee(comparison):
    guarantees = {r["algorithm"]: r["guarantees_cheapest"] for r in comparison["runs"]}
    assert guarantees == {"BFS": False, "UCS": True, "A*": True}


# --------------------------------------------------------------------------- #
# The results a FOAI course predicts
# --------------------------------------------------------------------------- #
def test_ucs_and_astar_agree_on_the_optimal_cost(comparison):
    runs = runs_by_name(comparison)
    assert runs["UCS"]["cost"] == runs["A*"]["cost"] == comparison["cheapest_cost"]
    assert runs["UCS"]["cheapest"] and runs["A*"]["cheapest"]


def test_bfs_is_never_cheaper_than_the_optimum(comparison):
    runs = runs_by_name(comparison)
    assert runs["BFS"]["cost"] >= runs["UCS"]["cost"]


def test_astar_expands_no_more_than_ucs(comparison):
    """The heuristic's whole job: same answer, less of the space explored."""
    runs = runs_by_name(comparison)
    assert runs["A*"]["expanded"] <= runs["UCS"]["expanded"]


def test_heuristic_is_admissible_on_this_query(comparison):
    h = comparison["heuristic"]
    assert h["admissible_here"] is True
    assert h["h_start"] <= runs_by_name(comparison)["A*"]["cost"]


# --------------------------------------------------------------------------- #
# Expansion order: the rule each algorithm uses, observed
# --------------------------------------------------------------------------- #
def _nondecreasing(values):
    values = [v for v in values if v is not None]
    return all(a <= b + 1e-9 for a, b in zip(values, values[1:]))


def test_bfs_expands_shallowest_first(comparison):
    order = runs_by_name(comparison)["BFS"]["expansion_order"]
    assert order and _nondecreasing([row["depth"] for row in order])


def test_ucs_expands_cheapest_g_first(comparison):
    order = runs_by_name(comparison)["UCS"]["expansion_order"]
    assert order and _nondecreasing([row["g"] for row in order])


def test_astar_expands_lowest_f_first(comparison):
    """Non-decreasing f is the observable sign of a CONSISTENT heuristic."""
    order = runs_by_name(comparison)["A*"]["expansion_order"]
    assert order and _nondecreasing([row["f"] for row in order])


def test_every_algorithm_starts_by_expanding_the_origin(comparison):
    for run in comparison["runs"]:
        first = run["expansion_order"][0]
        assert first["station"] == ORIGIN and first["depth"] == 0 and first["g"] == 0


# --------------------------------------------------------------------------- #
# Budget and inputs
# --------------------------------------------------------------------------- #
def test_one_budget_binds_all_three(store, heuristic):
    """A tiny budget stops every algorithm, and stopping is reported as such."""
    lo, _ = store.get_run_date_range()
    query = UserQuery(ORIGIN, DESTINATION, dt.date.fromisoformat(lo), max_transfers=1)
    result = compare_algorithms(store, heuristic, query, MODE, limit_expansions=3)
    for run in result["runs"]:
        assert run["expanded"] <= 3
        if not run["found"]:
            assert run["truncated"], f"{run['algorithm']} stopped at the budget but did not say so"
        assert not run["cheapest"] or not run["truncated"]


def test_same_train_comparison_searches_only_what_the_agent_searches(store, heuristic):
    """The coordinator caps SameTrainSearchAgent at one seat change. A
    comparison that ignored the cap would time journeys the app never offers."""
    from src.agents.coordinator_agent import SAME_TRAIN_MAX_TRANSFERS_CAP

    lo, _ = store.get_run_date_range()
    query = UserQuery(ORIGIN, DESTINATION, dt.date.fromisoformat(lo), max_transfers=2)
    result = compare_algorithms(store, heuristic, query, "same_train")
    assert result["requested_transfers"] == 2
    assert result["max_transfers"] == SAME_TRAIN_MAX_TRANSFERS_CAP
    for run in result["runs"]:
        assert len(run["legs"]) <= SAME_TRAIN_MAX_TRANSFERS_CAP + 1, run["algorithm"]
        assert all(row["depth"] <= SAME_TRAIN_MAX_TRANSFERS_CAP + 1 for row in run["expansion_order"])


def test_different_train_comparison_keeps_the_requested_transfers(store, heuristic):
    lo, _ = store.get_run_date_range()
    query = UserQuery(ORIGIN, DESTINATION, dt.date.fromisoformat(lo), max_transfers=2)
    result = compare_algorithms(store, heuristic, query, "different_train", limit_expansions=50)
    assert result["requested_transfers"] == result["max_transfers"] == 2


def test_unknown_mode_is_rejected(store, heuristic):
    lo, _ = store.get_run_date_range()
    query = UserQuery(ORIGIN, DESTINATION, dt.date.fromisoformat(lo))
    with pytest.raises(ValueError, match="mode"):
        compare_algorithms(store, heuristic, query, "teleport")


@pytest.mark.parametrize("journey_type,mode", [
    ("DIRECT", "same_train"),
    ("SAME_TRAIN_SEAT_CHANGE", "same_train"),
    ("DIFFERENT_TRAIN_CHANGE", "different_train"),
    (None, "same_train"),
])
def test_journey_type_maps_to_the_agent_that_found_it(journey_type, mode):
    assert mode_for_journey_type(journey_type) == mode
