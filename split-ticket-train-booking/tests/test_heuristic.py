"""
Admissibility tests for src.heuristic.RailHeuristic against real data.

The core property: for every real-halt stop X on train 12658's route, the
heuristic's estimate(X, 'MAS') must be <= the REAL cumulative travel time
12658 needs from X to MAS. The real cost is computed here independently, by
summing 12658's own segment times using journey_day arithmetic — it does not
go through rail_graph's edge weights.
"""

from __future__ import annotations

import math

import pytest

from src.data_store import Stop
from src.heuristic import RailHeuristic

MAIL = "12658"
GOAL = "MAS"
SPOT_CHECK_STOPS = ["BNC", "KPD", "PER"]


# --------------------------------------------------------------------------- #
# independent real-cost computation (does not use src.rail_graph)
# --------------------------------------------------------------------------- #
def _abs_minutes(clock: str, journey_day: int) -> int:
    h, m = clock.split(":")[:2]
    return journey_day * 1440 + int(h) * 60 + int(m)


def _real_segment(a: Stop, b: Stop) -> int:
    assert a.departure is not None and b.arrival is not None
    assert a.journey_day is not None and b.journey_day is not None
    return _abs_minutes(b.arrival, b.journey_day) - _abs_minutes(a.departure, a.journey_day)


def _real_cost_to_destination(stops: list[Stop]) -> dict[str, int]:
    """{station_code: minutes from that stop's departure to final arrival}, by summing segments."""
    segments = [_real_segment(a, b) for a, b in zip(stops, stops[1:])]
    assert all(s > 0 for s in segments), segments
    remaining = 0
    cost = {stops[-1].station_code: 0}
    for stop, seg in zip(reversed(stops[:-1]), reversed(segments)):
        remaining += seg
        cost[stop.station_code] = remaining
    return cost


@pytest.fixture(scope="module")
def mail_real_costs(store):
    stops = store.get_real_halt_stops(MAIL)
    assert stops[-1].station_code == GOAL
    return _real_cost_to_destination(stops)


# --------------------------------------------------------------------------- #
# admissibility
# --------------------------------------------------------------------------- #
def test_real_cost_sanity(store, mail_real_costs):
    # 12658 leaves SBC 22:45 day 1, arrives MAS 04:40 day 2 -> 355 min wall-clock.
    # Summed segment times exclude dwell at the 6 intermediate halts (15 min),
    # so the moving-time cost from SBC is 340. Dwell belongs to nodes, not edges.
    stops = store.get_real_halt_stops(MAIL)
    wall_clock = _abs_minutes(stops[-1].arrival, stops[-1].journey_day) - _abs_minutes(
        stops[0].departure, stops[0].journey_day
    )
    dwell = sum(s.halt_minutes for s in stops[1:-1])
    assert wall_clock == 355
    assert dwell == 15
    assert mail_real_costs["SBC"] == wall_clock - dwell == 340
    assert mail_real_costs[GOAL] == 0
    # costs strictly decrease along the route
    order = ["SBC", "BNC", "BWT", "JTJ", "KPD", "AJJ", "PER", "MAS"]
    costs = [mail_real_costs[c] for c in order]
    assert costs == sorted(costs, reverse=True)


def test_heuristic_is_admissible_for_every_12658_halt(heuristic, mail_real_costs):
    print(f"\n{'stop':<6}{'h(stop, MAS)':>14}{'real (12658)':>14}{'slack':>8}")
    for code, real in mail_real_costs.items():
        est = heuristic.estimate(code, GOAL)
        print(f"{code:<6}{est:>14.0f}{real:>14}{real - est:>8.0f}")
        assert est <= real, f"INADMISSIBLE at {code}: heuristic {est} > real {real}"
        assert est >= 0


@pytest.mark.parametrize("code", SPOT_CHECK_STOPS)
def test_heuristic_spot_checks(heuristic, mail_real_costs, code):
    est = heuristic.estimate(code, GOAL)
    real = mail_real_costs[code]
    print(f"\n{code} -> {GOAL}: heuristic={est:.0f} min, real={real} min, slack={real - est:.0f}")
    assert math.isfinite(est)
    assert 0 < est <= real


def test_heuristic_is_admissible_for_many_trains(heuristic, store):
    """Broader check: every real train's own route cost must dominate the heuristic."""
    checked = 0
    for tn in store.get_all_train_numbers()[:600]:
        stops = store.get_real_halt_stops(tn)
        if len(stops) < 2:
            continue
        if any(a.departure is None or b.arrival is None or a.journey_day is None or b.journey_day is None
               for a, b in zip(stops, stops[1:])):
            continue
        segments = [_real_segment(a, b) for a, b in zip(stops, stops[1:])]
        if any(s <= 0 for s in segments):
            continue  # inconsistent source data; graph skipped these edges too
        goal = stops[-1].station_code
        remaining = 0
        for stop, seg in zip(reversed(stops[:-1]), reversed(segments)):
            remaining += seg
            est = heuristic.estimate(stop.station_code, goal)
            assert est <= remaining, f"train {tn}: h({stop.station_code},{goal})={est} > real {remaining}"
            checked += 1
    print(f"\nchecked {checked} (stop, goal) pairs across the first 600 trains")
    assert checked > 1000


# --------------------------------------------------------------------------- #
# identity / unreachable / caching
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("code", ["SBC", "MAS", "KPD", "NOT_A_STATION"])
def test_estimate_same_station_is_zero(heuristic, code):
    assert heuristic.estimate(code, code) == 0.0


def test_estimate_unknown_station_is_inf(heuristic):
    assert heuristic.estimate("NOT_A_STATION", GOAL) == float("inf")
    assert heuristic.estimate("SBC", "NOT_A_STATION") == float("inf")


def test_estimate_no_path_between_real_stations_is_inf(graph, heuristic):
    # A node with no outgoing edges can't reach anything. Pick one deterministically.
    sinks = sorted(n for n, deg in graph.out_degree() if deg == 0)
    assert sinks, "expected at least one sink node in the graph"
    sink = sinks[0]
    target = GOAL if sink != GOAL else "SBC"
    assert heuristic.estimate(sink, target) == float("inf")


def test_estimate_matches_direct_edge_when_it_is_the_shortest(graph, heuristic):
    # PER -> MAS is a direct edge; heuristic can't be larger than that edge.
    assert heuristic.estimate("PER", GOAL) <= graph["PER"][GOAL]["weight"]


def test_dijkstra_runs_once_per_goal(graph):
    h = RailHeuristic(graph)
    assert h.cached_goals == []
    h.estimate("SBC", GOAL)
    h.estimate("BNC", GOAL)
    h.estimate("KPD", GOAL)
    assert h.cached_goals == [GOAL]
    h.estimate("MAS", "SBC")
    assert sorted(h.cached_goals) == sorted([GOAL, "SBC"])
    h.clear_cache()
    assert h.cached_goals == []
