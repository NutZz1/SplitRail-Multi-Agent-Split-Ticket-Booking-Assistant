"""
Station graph: the relaxed network used for the A* heuristic.

Nodes are station codes. A directed edge A -> B exists when at least one
train halts at A and then halts next at B (pass-through stops in between are
ignored, because you cannot board or alight there). The edge weight is the
FASTEST real scheduled travel time in minutes from A's departure to B's
arrival across every train that runs that segment.

Taking the minimum over all trains is what makes this graph a *relaxation*
of the real problem: it ignores which train you're on, whether it runs that
day, and whether a seat is free. Any real itinerary is therefore a path in
this graph with cost >= the graph's shortest path, which is exactly what an
admissible A* heuristic needs (see :mod:`src.heuristic`).

Edge weights are travel time only — no fare component yet.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import networkx as nx

from src.data_store import RailDataStore, Stop

logger = logging.getLogger(__name__)

MINUTES_PER_DAY = 1440


# --------------------------------------------------------------------------- #
# Time arithmetic
# --------------------------------------------------------------------------- #
def time_to_minutes(hhmmss: str) -> int:
    """Convert a ``HH:MM:SS`` (or ``HH:MM``) clock string to minutes since midnight."""
    parts = hhmmss.split(":")
    return int(parts[0]) * 60 + int(parts[1])


def segment_minutes(a: Stop, b: Stop) -> Optional[int]:
    """Travel time in minutes from ``a``'s departure to ``b``'s arrival.

    Uses ``journey_day`` when both stops carry it, so multi-day trains are
    handled exactly. If either day is missing, falls back to clock arithmetic
    and assumes a single midnight rollover when ``b.arrival < a.departure``.

    Returns ``None`` if either timestamp is missing. May return a zero or
    negative number when the source data is inconsistent — callers decide
    what to do with those (:func:`build_graph` skips them).
    """
    if a.departure is None or b.arrival is None:
        return None

    dep = time_to_minutes(a.departure)
    arr = time_to_minutes(b.arrival)

    if a.journey_day is not None and b.journey_day is not None:
        return (b.journey_day * MINUTES_PER_DAY + arr) - (a.journey_day * MINUTES_PER_DAY + dep)

    delta = arr - dep
    if delta < 0:
        delta += MINUTES_PER_DAY
    return delta


# --------------------------------------------------------------------------- #
# Graph construction
# --------------------------------------------------------------------------- #
@dataclass
class GraphBuildStats:
    """Counters collected while building the graph, for the summary printout."""

    trains_seen: int = 0
    trains_contributing: int = 0
    pairs_seen: int = 0
    skipped_missing_time: int = 0
    skipped_nonpositive: int = 0
    edges_added: int = 0
    edges_relaxed: int = 0  # an existing edge was replaced by a faster train

    def summary(self, graph: nx.DiGraph) -> str:
        return (
            f"Rail graph: {graph.number_of_nodes():,} nodes, "
            f"{graph.number_of_edges():,} edges, "
            f"{self.trains_contributing:,}/{self.trains_seen:,} trains contributed "
            f"(pairs seen {self.pairs_seen:,}; skipped {self.skipped_missing_time} missing-time, "
            f"{self.skipped_nonpositive} non-positive; {self.edges_relaxed:,} edges relaxed to a faster train)"
        )


def build_graph(store: RailDataStore, *, verbose: bool = True) -> nx.DiGraph:
    """Build the minimum-travel-time station graph from every train in ``store``.

    Node attributes:
        ``name``  - station name as it appears in the schedule (debug aid)
    Edge attributes:
        ``weight``        - fastest travel time in minutes (Dijkstra weight)
        ``train_number``  - the train that achieves that fastest time
        ``train_count``   - how many distinct trains run this segment

    Prints a one-line summary when ``verbose`` is true. The stats object is
    also attached as ``graph.graph["build_stats"]``.
    """
    graph = nx.DiGraph()
    stats = GraphBuildStats()

    for train_number in store.get_all_train_numbers():
        stats.trains_seen += 1
        stops = store.get_real_halt_stops(train_number)
        contributed = False

        for a, b in zip(stops, stops[1:]):
            stats.pairs_seen += 1
            minutes = segment_minutes(a, b)

            if minutes is None:
                stats.skipped_missing_time += 1
                logger.debug(
                    "train %s: %s -> %s skipped, missing departure/arrival",
                    train_number, a.station_code, b.station_code,
                )
                continue
            if minutes <= 0:
                stats.skipped_nonpositive += 1
                logger.warning(
                    "train %s: %s (dep %s day %s) -> %s (arr %s day %s) gives %d min, skipping",
                    train_number, a.station_code, a.departure, a.journey_day,
                    b.station_code, b.arrival, b.journey_day, minutes,
                )
                continue

            u, v = a.station_code, b.station_code
            graph.add_node(u, name=a.station_name)
            graph.add_node(v, name=b.station_name)

            existing = graph.get_edge_data(u, v)
            if existing is None:
                graph.add_edge(u, v, weight=minutes, train_number=train_number, train_count=1)
                stats.edges_added += 1
            else:
                existing["train_count"] += 1
                if minutes < existing["weight"]:
                    existing["weight"] = minutes
                    existing["train_number"] = train_number
                    stats.edges_relaxed += 1
            contributed = True

        if contributed:
            stats.trains_contributing += 1

    graph.graph["build_stats"] = stats
    if verbose:
        print(stats.summary(graph))
    return graph
