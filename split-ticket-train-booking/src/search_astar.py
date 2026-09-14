"""
A* search over journey states, ordered by f(n) = g(n) + h(n).

g(n) is ``cumulative_time_minutes`` (moving time, the same units as the
station-graph edge weights) and h(n) is
``RailHeuristic.estimate(current_station, destination)``, the graph's
shortest-path lower bound. Because h is admissible (validated in
``tests/test_heuristic.py``), the first goal popped is time-optimal.

A station the graph cannot connect to the destination gets h = ``inf``,
hence f = ``inf``. ``heapq`` orders floats, and ``inf`` compares greater
than every finite f, so such entries simply sink to the bottom of the heap
and are only popped once every finite-f state is exhausted. No special
casing is needed; ``tests/test_search_astar.py`` confirms the ordering.

Ties on f are broken FIFO through an insertion counter, mirroring UCS, so
the two are comparable expansion-for-expansion.
"""

from __future__ import annotations

import heapq
import itertools
import time
from typing import Callable

from src.data_store import RailDataStore
from src.goal import is_goal
from src.heuristic import RailHeuristic
from src.search_stats import SearchStats
from src.state import JourneyState, UserQuery, initial_state
from src.successors import close_final_ticket, get_successors

#: Signature every successor generator must satisfy.
SuccessorsFn = Callable[[JourneyState, UserQuery, RailDataStore], list[JourneyState]]


def astar_search(
    query: UserQuery,
    store: RailDataStore,
    heuristic: RailHeuristic,
    successors_fn: SuccessorsFn = get_successors,
) -> tuple[JourneyState | None, SearchStats]:
    """A* search with the rail-graph heuristic. Returns ``(goal_state, stats)`` or ``(None, stats)``.

    ``successors_fn`` selects the move generator: the default is the
    same-train generator; pass
    :func:`src.successors_different_train.get_successors_different_train`
    to search across trains. The search loop itself is identical.
    """
    stats = SearchStats(algorithm="A*")
    t0 = time.perf_counter()
    goal_station = query.destination_station

    def f(state: JourneyState) -> float:
        return state.cumulative_time_minutes + heuristic.estimate(state.current_station, goal_station)

    start = initial_state(query)
    counter = itertools.count()
    frontier: list[tuple[float, int, JourneyState]] = [(f(start), next(counter), start)]
    best_g: dict[JourneyState, float] = {start: 0.0}
    finalized: set[JourneyState] = set()
    parents: dict[JourneyState, JourneyState | None] = {start: None}
    stats.note_frontier(1)

    while frontier:
        _, _, state = heapq.heappop(frontier)
        if state in finalized:
            continue
        finalized.add(state)

        if is_goal(state, query):
            stats.finish_found(state, parents, finalized=close_final_ticket(state, query, store))
            stats.wall_clock_seconds = time.perf_counter() - t0
            return stats.path[-1], stats  # type: ignore[index]

        stats.nodes_expanded += 1
        for child in successors_fn(state, query, store):
            stats.nodes_generated += 1
            child_g = child.cumulative_time_minutes
            if child in finalized:
                continue
            known = best_g.get(child)
            if known is not None and known <= child_g:
                continue
            best_g[child] = child_g
            parents[child] = state
            heapq.heappush(frontier, (f(child), next(counter), child))
        stats.note_frontier(len(frontier))

    stats.finish_not_found()
    stats.wall_clock_seconds = time.perf_counter() - t0
    return None, stats
