"""
Make the search strategy visible: BFS, UCS and A* side by side on ONE query.

The agents only ever run A*. That is the right engineering choice and the
wrong teaching artefact: on its own, an A* result cannot show *why* A* was
chosen. :func:`compare_algorithms` re-runs the user's actual query three
times, once per algorithm, so the interface can show the trade-off instead of
asserting it.

What keeps the comparison honest
--------------------------------
* **Same problem.** All three search the same state space with the SAME
  successor function the search agents use (:class:`src.seat_search.SeatSuccessors`),
  so differences come from the algorithm, not from a simplified toy graph.
* **Same budget.** One expansion/time limit for all three
  (:func:`src.search_stats.search_budget`). A run that hits it is reported as
  stopped, never as "no route", because it proved nothing.
* **Nothing hard-coded.** Every number on screen -- expansions, costs, the
  heuristic estimate, the order nodes were expanded in -- is measured here on
  this query. Change the data, weights or code and the explanation changes
  with them.

Expansion order
---------------
The textbook distinction between the three is the order they take nodes off
the frontier: BFS by depth, UCS by path cost g, A* by f = g + h. The successor
function is called exactly once per expansion, so wrapping it records that
order without touching any search loop. The recorded rows let the interface
show the rule in action -- and let the tests check it (BFS depth, UCS g and
A* f are each non-decreasing along their own expansion order).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

from src.agents.coordinator_agent import same_train_query
from src.search_astar import astar_search
from src.search_bfs import bfs_search
from src.search_ucs import ucs_search
from src.seat_search import SeatSuccessors
from src.state import JourneyState, UserQuery

#: Budget for the interactive comparison. Deliberately far below the agents'
#: own limit (src.config): three searches run one after another while the user
#: waits, and the point is to show the ORDERING behaviour, which is visible
#: long before 2,500 expansions.
EXPLAIN_EXPANSIONS = 2500
EXPLAIN_SECONDS = 2.5

#: How many expansions per algorithm to show in the "expansion order" table.
ORDER_ROWS = 8

#: Which search agent explores which part of the space.
AGENT_FOR_MODE = {
    "same_train": "SameTrainSearchAgent",
    "different_train": "DifferentTrainSearchAgent",
}

#: The one-line rule each algorithm uses to pick the next node. Shown verbatim.
ORDERING = {
    "BFS": "fewest tickets first (depth)",
    "UCS": "lowest travel time so far, g(n)",
    "A*": "lowest f(n) = g(n) + h(n)",
}


@dataclass
class _ExpansionRecorder:
    """Wraps a successor function and records the states it is asked to expand.

    Every BFS/UCS/A* loop calls the successor function exactly once per
    expansion, on the node just taken from the frontier, so the call order IS
    the expansion order.
    """

    inner: Callable
    limit: int = ORDER_ROWS
    order: list[JourneyState] = field(default_factory=list)

    def __call__(self, state, query, store):
        if len(self.order) < self.limit:
            self.order.append(state)
        return self.inner(state, query, store)


def _finite(value: float | None) -> float | None:
    """JSON has no infinity; an unreachable station's h is reported as None."""
    if value is None or not math.isfinite(value):
        return None
    return round(value, 1)


def _legs(goal: JourneyState | None) -> list[dict]:
    if goal is None:
        return []
    return [{"train_number": t.train_number, "from_station": t.from_station,
             "to_station": t.to_station, "travel_class": t.travel_class,
             "departure": t.boarding_datetime.isoformat(),
             "arrival": t.alighting_datetime.isoformat()}
            for t in goal.tickets_so_far]


def _order_rows(order: list[JourneyState], query: UserQuery, heuristic) -> list[dict]:
    rows = []
    for state in order:
        g = state.cumulative_time_minutes
        h = heuristic.estimate(state.current_station, query.destination_station)
        # The same station recurs with different trains/seats -- those are
        # different states -- so the train is shown to tell them apart.
        rows.append({
            "station": state.current_station,
            "train": state.current_train,
            "depth": len(state.tickets_so_far),
            "g": _finite(g),
            "h": _finite(h),
            "f": _finite(g + h),
        })
    return rows


def compare_algorithms(
    store,
    heuristic,
    query: UserQuery,
    mode: str,
    *,
    limit_expansions: int = EXPLAIN_EXPANSIONS,
    limit_seconds: float = EXPLAIN_SECONDS,
) -> dict:
    """Run BFS, UCS and A* on ``query`` in one search agent's state space.

    ``mode`` is ``"same_train"`` or ``"different_train"`` -- the two spaces the
    two search agents explore. Returns plain data for the interface: the
    problem's heuristic at the start state, then one entry per algorithm.
    """
    if mode not in AGENT_FOR_MODE:
        raise ValueError(f"mode must be one of {', '.join(AGENT_FOR_MODE)}.")
    requested_transfers = query.max_transfers
    if mode == "same_train":
        # The coordinator never lets SameTrainSearchAgent search more than one
        # seat change (same_train_query caps it). The comparison must search
        # exactly the space that agent searches, or it would describe -- and
        # time -- journeys the app can never offer.
        query = same_train_query(query)
    budget = {"limit_expansions": limit_expansions, "limit_seconds": limit_seconds}
    runners = {
        "BFS": lambda fn: bfs_search(query, store, successors_fn=fn, **budget),
        "UCS": lambda fn: ucs_search(query, store, successors_fn=fn, **budget),
        "A*": lambda fn: astar_search(query, store, heuristic, successors_fn=fn, **budget),
    }

    runs = []
    for name, run in runners.items():
        recorder = _ExpansionRecorder(SeatSuccessors(store, query, mode))
        goal, stats = run(recorder)
        runs.append({
            "algorithm": name,
            "ordering": ORDERING[name],
            "guarantees_cheapest": name != "BFS",
            "found": goal is not None,
            "truncated": stats.truncated,
            "expanded": stats.nodes_expanded,
            "generated": stats.nodes_generated,
            "max_frontier": stats.max_frontier_size,
            "cost": _finite(stats.total_cost),
            "milliseconds": round(stats.wall_clock_seconds * 1000, 1),
            "legs": _legs(goal),
            "expansion_order": _order_rows(recorder.order, query, heuristic),
        })

    # "Cheapest" is only meaningful among runs that finished: a truncated run
    # may have stopped before its best answer.
    finished = [r["cost"] for r in runs if r["found"] and not r["truncated"]]
    best = min(finished) if finished else None
    for r in runs:
        r["cheapest"] = best is not None and r["cost"] == best

    h_start = heuristic.estimate(query.origin_station, query.destination_station)
    astar = runs[2]
    return {
        "mode": mode,
        "agent": AGENT_FOR_MODE[mode],
        # What the user asked for, and what this agent actually searched.
        "requested_transfers": requested_transfers,
        "max_transfers": query.max_transfers,
        "budget": {"expansions": limit_expansions, "seconds": limit_seconds},
        "heuristic": {
            "name": "fastest possible remaining time on the relaxed station graph",
            "start_station": query.origin_station,
            "goal_station": query.destination_station,
            "h_start": _finite(h_start),
            # Admissibility, checked live on this query: the estimate at the
            # start must not exceed the true optimal cost A* found.
            "admissible_here": (astar["cost"] is None or h_start is None
                                or not math.isfinite(h_start) or h_start <= astar["cost"] + 1e-6),
        },
        "runs": runs,
        "cheapest_cost": best,
    }


def mode_for_journey_type(journey_type: str | None) -> str:
    """The search agent whose space produced an option of this kind."""
    return "different_train" if journey_type == "DIFFERENT_TRAIN_CHANGE" else "same_train"
