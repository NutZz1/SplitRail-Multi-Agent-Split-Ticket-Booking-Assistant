"""
Admissible A* heuristic backed by the relaxed station graph.

:class:`RailHeuristic` answers "what is a lower bound on the travel time from
station X to the goal?" by running Dijkstra on the graph from
:mod:`src.rail_graph`. Because every edge in that graph carries the fastest
real scheduled time for its segment, the shortest path in it can never be
longer than any real itinerary, so the estimate is admissible.

Known limitation
----------------
The graph only contains segments between consecutive *real halts* of trains
present in the dataset. Stations that no train halts at (pure pass-throughs,
or stations with no schedule data) are not nodes, and some node pairs are
simply not connected. For those, :meth:`RailHeuristic.estimate` returns
``inf``. That is a coverage gap in the data, not an error; A* will treat such
states as unreachable, which is the correct conservative behaviour.
"""

from __future__ import annotations

import networkx as nx


class RailHeuristic:
    """Lazily-computed, cached minimum-time lower bounds on a station graph.

    Dijkstra is run at most once per *goal* station, on the reversed graph,
    which yields the distance from every reachable station *to* the goal in a
    single pass. Results are cached, so repeated ``estimate(x, goal)`` calls
    during one A* search cost a dictionary lookup.
    """

    def __init__(self, graph: nx.DiGraph) -> None:
        self._graph = graph
        # A reversed view shares data with the original graph; no copy is made.
        self._reversed = graph.reverse(copy=False)
        self._cache: dict[str, dict[str, float]] = {}

    # -- public API ---------------------------------------------------------
    def estimate(self, from_station: str, goal_station: str) -> float:
        """Lower bound (minutes) on travel time from ``from_station`` to ``goal_station``.

        Returns ``0.0`` when the two stations are the same and ``inf`` when
        the graph contains no path between them (see module docstring).
        """
        if from_station == goal_station:
            return 0.0
        return self._distances_to(goal_station).get(from_station, float("inf"))

    def clear_cache(self) -> None:
        """Drop all cached Dijkstra results (e.g. if the graph was rebuilt)."""
        self._cache.clear()

    @property
    def cached_goals(self) -> list[str]:
        """Goal stations for which Dijkstra has already been run."""
        return list(self._cache)

    # -- internals ----------------------------------------------------------
    def _distances_to(self, goal_station: str) -> dict[str, float]:
        """Distance from every station that can reach ``goal_station``, cached."""
        cached = self._cache.get(goal_station)
        if cached is not None:
            return cached

        if goal_station in self._reversed:
            distances = nx.single_source_dijkstra_path_length(
                self._reversed, goal_station, weight="weight"
            )
        else:
            # Goal is not in the graph at all: nothing can reach it.
            distances = {}

        self._cache[goal_station] = distances
        return distances
