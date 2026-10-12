"""
Breadth-first search over journey states.

BFS explores by *number of moves* (board / continue / transfer), not by
travel time. The first goal it pops is the one reachable in the fewest
moves, which is not in general the fastest journey. That is the intended
contrast with UCS/A*, not a defect. Because ``JourneyState`` carries
``arrival_datetime``, ``cumulative_time_minutes`` and ``tickets_so_far``,
two states at the same station reached by different histories are
different states, so the visited-set only removes exact duplicates.
"""

from __future__ import annotations

import time
from typing import Callable
from collections import deque

from src.data_store import RailDataStore
from src.goal import is_goal
from src.search_stats import SearchStats, search_budget
from src.state import JourneyState, UserQuery, initial_state
from src.successors import close_final_ticket, get_successors

#: Signature every successor generator must satisfy.
SuccessorsFn = Callable[[JourneyState, UserQuery, RailDataStore], list[JourneyState]]


def bfs_search(
    query: UserQuery, store: RailDataStore, successors_fn: SuccessorsFn = get_successors,
    *, limit_expansions: int | None = None, limit_seconds: float | None = None,
) -> tuple[JourneyState | None, SearchStats]:
    """FIFO breadth-first search. Returns ``(goal_state, stats)`` or ``(None, stats)``.

    ``successors_fn`` selects the move generator (default: same-train).

    Bounded by the same budget as A* (``src.config.MAX_SEARCH_EXPANSIONS`` /
    ``SEARCH_SECONDS`` unless overridden). BFS used to be unbounded, which was
    harmless on the six-train data set but on the full Kerala graph could run
    for minutes. Hitting the budget sets ``stats.truncated``: the search
    stopped, it did not prove that no journey exists.
    """
    stats = SearchStats(algorithm="BFS")
    t0 = time.perf_counter()
    max_expanded, max_seconds = search_budget(limit_expansions, limit_seconds)

    start = initial_state(query)
    frontier: deque[JourneyState] = deque([start])
    visited: set[JourneyState] = {start}
    parents: dict[JourneyState, JourneyState | None] = {start: None}
    stats.note_frontier(1)

    while frontier:
        if stats.nodes_expanded >= max_expanded or time.perf_counter() - t0 >= max_seconds:
            stats.truncated = True
            break
        state = frontier.popleft()
        if is_goal(state, query):
            stats.finish_found(state, parents, finalized=close_final_ticket(state, query, store))
            stats.wall_clock_seconds = time.perf_counter() - t0
            return stats.path[-1], stats  # type: ignore[index]

        stats.nodes_expanded += 1
        for child in successors_fn(state, query, store):
            stats.nodes_generated += 1
            if child in visited:
                continue
            visited.add(child)
            parents[child] = state
            frontier.append(child)
        stats.note_frontier(len(frontier))

    stats.finish_not_found()
    stats.wall_clock_seconds = time.perf_counter() - t0
    return None, stats
