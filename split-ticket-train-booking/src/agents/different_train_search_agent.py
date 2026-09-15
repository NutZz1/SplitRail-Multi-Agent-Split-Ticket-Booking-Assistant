"""
DifferentTrainSearchAgent: finds an itinerary that may change trains at a
real halt with enough connection time (a "different-train split ticket").

Same shape and concurrency model as :class:`SameTrainSearchAgent` -- A* in
a worker thread via ``asyncio.to_thread`` with a per-thread
``RailDataStore.clone()`` -- but the search is driven by
:func:`src.successors_different_train.get_successors_different_train`.
"""

from __future__ import annotations

import asyncio

from src.data_store import RailDataStore
from src.heuristic import RailHeuristic
from src.proposal import ItineraryProposal
from src.search_astar import astar_search
from src.agents.worker_pool import SearchWorkerPool
from src.search_stats import SearchStats
from src.state import JourneyState, UserQuery
from src.successors_different_train import (
    MIN_DIFFERENT_TRAIN_BUFFER_MINUTES,
    get_successors_different_train,
)


class DifferentTrainSearchAgent:
    name = "DifferentTrainSearchAgent"
    search_mode = "different_train"

    def __init__(
        self,
        store: RailDataStore,
        heuristic: RailHeuristic,
        pool: SearchWorkerPool | None = None,
    ) -> None:
        """``pool`` (recommended) runs the search in a worker process so several
        agents can genuinely run in parallel; without it the search runs in a
        thread, which keeps the event loop responsive but, being GIL-bound,
        does not overlap with other searches."""
        self._store = store
        self._heuristic = heuristic
        self._pool = pool

    async def propose(self, query: UserQuery) -> ItineraryProposal:
        """Search for an itinerary allowing train changes, without blocking the event loop."""
        if self._pool is not None:
            goal, stats = await self._pool.search(query, self.search_mode)
        else:
            goal, stats = await asyncio.to_thread(self._search, query)
        reason = None if goal is not None else await asyncio.to_thread(self._diagnose, query, stats)
        return ItineraryProposal.from_search(self.name, query, goal, stats, failure_reason=reason)

    # -- worker-thread bodies (each opens its own DB connection) -------------
    def _search(self, query: UserQuery) -> tuple[JourneyState | None, SearchStats]:
        with self._store.clone() as store:
            return astar_search(query, store, self._heuristic, successors_fn=get_successors_different_train)

    def _diagnose(self, query: UserQuery, stats: SearchStats) -> str:
        with self._store.clone() as store:
            return diagnose_failure(query, store, stats)


def diagnose_failure(query: UserQuery, store: RailDataStore, stats: SearchStats) -> str:
    """Explain why a different-train search found nothing.

    The first three cases are exact. The last two are lumped by necessity:
    once boarding succeeded, the search does not record whether each branch
    died on availability, on connection time, on the transfer budget, or
    simply because no running train that reaches the destination shares a
    halt with the boarded train(s).
    """
    origin, dest, date = query.origin_station, query.destination_station, query.travel_date.isoformat()

    halting = [
        tn for tn in store.get_all_trains_through_station(origin)
        if any(s.station_code == origin for s in store.get_real_halt_stops(tn))
    ]
    if not halting:
        return f"No train has a real halt at {origin}."

    running = [tn for tn in halting if store.runs_on_date(tn, date)]
    if not running:
        return f"None of the {len(halting)} train(s) halting at {origin} run on {date}."

    if stats.nodes_generated == 0:
        cls = f" in class {query.travel_class_preference}" if query.class_is_hard_constraint else ""
        return f"Train(s) {', '.join(running)} run from {origin} on {date} but no coach had availability at boarding{cls}."

    return (
        f"Boarded {', '.join(running)} from {origin} but no itinerary reached {dest}: no connecting "
        f"train (running {date}, reaching {dest}, departing >= {MIN_DIFFERENT_TRAIN_BUFFER_MINUTES:.0f} min "
        f"after arrival, with a bookable seat) was found within max_transfers={query.max_transfers}"
        f"{' and class ' + str(query.travel_class_preference) if query.class_is_hard_constraint else ''}. "
        f"Searched {stats.nodes_expanded} states."
    )
