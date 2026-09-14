"""
Uniform-cost search over journey states, ordered by g(n) = cumulative moving time.

Standard Dijkstra-style UCS: the frontier is a min-heap on g; a state is
finalised the first time it is popped, and any later heap entry for a
state already finalised at a lower-or-equal cost is discarded. Ties on g
are broken FIFO via an insertion counter so the heap never compares
``JourneyState`` objects.

Note that ``cumulative_time_minutes`` is itself a field of ``JourneyState``,
so two entries for "the same" state necessarily have the same g; the
lower-cost check therefore only ever triggers on exact duplicates. It is
kept because it is the textbook invariant and will matter if the state
representation is ever slimmed down.
"""

from __future__ import annotations

import heapq
import itertools
import time

from src.data_store import RailDataStore
from src.goal import is_goal
from src.search_stats import SearchStats
from src.state import JourneyState, UserQuery, initial_state
from src.successors import close_final_ticket, get_successors


def ucs_search(query: UserQuery, store: RailDataStore) -> tuple[JourneyState | None, SearchStats]:
    """Uniform-cost search. Returns ``(goal_state, stats)`` or ``(None, stats)``."""
    stats = SearchStats(algorithm="UCS")
    t0 = time.perf_counter()

    start = initial_state(query)
    counter = itertools.count()
    frontier: list[tuple[float, int, JourneyState]] = [(0.0, next(counter), start)]
    best_g: dict[JourneyState, float] = {start: 0.0}
    finalized: set[JourneyState] = set()
    parents: dict[JourneyState, JourneyState | None] = {start: None}
    stats.note_frontier(1)

    while frontier:
        g, _, state = heapq.heappop(frontier)
        if state in finalized:
            continue  # stale entry: already expanded at <= this cost
        finalized.add(state)

        if is_goal(state, query):
            stats.finish_found(state, parents, finalized=close_final_ticket(state, query, store))
            stats.wall_clock_seconds = time.perf_counter() - t0
            return stats.path[-1], stats  # type: ignore[index]

        stats.nodes_expanded += 1
        for child in get_successors(state, query, store):
            stats.nodes_generated += 1
            child_g = child.cumulative_time_minutes
            if child in finalized:
                continue
            known = best_g.get(child)
            if known is not None and known <= child_g:
                continue
            best_g[child] = child_g
            parents[child] = state
            heapq.heappush(frontier, (child_g, next(counter), child))
        stats.note_frontier(len(frontier))

    stats.finish_not_found()
    stats.wall_clock_seconds = time.perf_counter() - t0
    return None, stats
