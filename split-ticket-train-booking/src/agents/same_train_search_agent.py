"""
SameTrainSearchAgent: finds an itinerary that stays on one train, possibly
changing coach at a long-enough halt (a "same-train split ticket").

Internally it runs A* (:func:`src.search_astar.astar_search`) over the
same-train successor function from Step 3. It exposes an ``async``
interface so that a coordinator can run it alongside other agents with
``asyncio.gather()``.

Concurrency model
-----------------
``propose()`` never blocks the event loop. With a :class:`SearchWorkerPool`
the search runs in a worker process (its own store + heuristic), and
several agents' ``propose()`` calls gathered together genuinely run in
parallel -- measured at ~0.55-0.8x of back-to-back time. Without a pool the
search runs in a thread via ``asyncio.to_thread``: still non-blocking, but
because the search is pure CPU-bound Python (the DB is opened
``immutable=1``, so no query releases the GIL) two threaded searches do
not overlap. See ``tests/test_same_train_search_agent.py`` for numbers.

Each thread-mode call opens its **own** ``RailDataStore`` connection via
:meth:`RailDataStore.clone`: ``sqlite3`` connections are bound to their
creating thread.

The shared :class:`RailHeuristic` is read-mostly (its per-goal Dijkstra
cache is filled on first use); concurrent misses at worst recompute the
same table twice, which is harmless.

Exceptions from the search are deliberately not caught: a crash should
surface in tests, not be hidden behind a "not found" proposal.
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
from src.successors import MIN_COACH_SWITCH_MINUTES


class SameTrainSearchAgent:
    name = "SameTrainSearchAgent"
    search_mode = "same_train"

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
        """Search for a same-train itinerary for ``query`` without blocking the event loop."""
        if self._pool is not None:
            goal, stats = await self._pool.search(query, self.search_mode)
        else:
            goal, stats = await asyncio.to_thread(self._search, query)
        reason = None if goal is not None else await asyncio.to_thread(self._diagnose, query, stats)
        return ItineraryProposal.from_search(self.name, query, goal, stats, failure_reason=reason)

    # -- worker-thread bodies (each opens its own DB connection) -------------
    def _search(self, query: UserQuery) -> tuple[JourneyState | None, SearchStats]:
        with self._store.clone() as store:
            return astar_search(query, store, self._heuristic)

    def _diagnose(self, query: UserQuery, stats: SearchStats) -> str:
        with self._store.clone() as store:
            return diagnose_failure(query, store, stats)


def diagnose_failure(query: UserQuery, store: RailDataStore, stats: SearchStats) -> str:
    """Explain why a same-train search found nothing.

    The first four cases are determined exactly from the data. The last one
    lumps together "availability ran out mid-route" and "no halt was long
    enough (or no transfer budget) to switch coach": the search does not
    record *which* prune fired on which branch, and separating them would
    require re-running the successor generator with individual constraints
    relaxed. The message names both possibilities.
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

    reaching = []
    for tn in running:
        codes = [s.station_code for s in store.get_real_halt_stops(tn)]
        if dest in codes and codes.index(dest) > codes.index(origin):
            reaching.append(tn)
    if not reaching:
        return (
            f"No single train runs {origin} -> {dest} on {date} "
            f"(same-train search only; trains running from {origin}: {', '.join(running)})."
        )

    if query.class_is_hard_constraint:
        offering = [
            tn for tn in reaching
            if (t := store.get_train(tn)) is not None and query.travel_class_preference in t.classes
        ]
        if not offering:
            return (
                f"Class {query.travel_class_preference} is not offered on any train running "
                f"{origin} -> {dest} on {date} ({', '.join(reaching)})."
            )

    if stats.nodes_generated == 0:
        # A train exists, runs, reaches, and offers the class -- but nothing
        # could be boarded, i.e. no coach had a bookable seat on the first segment.
        with_seats = "no coach had availability at boarding"
        if query.class_is_hard_constraint:
            with_seats += f" in class {query.travel_class_preference}"
        return f"Train(s) {', '.join(reaching)} run {origin} -> {dest} on {date} but {with_seats}."

    # Lumped case (see docstring): boarded but every branch died before the goal.
    return (
        f"Boarded {', '.join(reaching)} but no branch reached {dest}: seat availability ran out "
        f"mid-route and no halt of >= {MIN_COACH_SWITCH_MINUTES:.0f} min (within max_transfers="
        f"{query.max_transfers}) allowed a coach switch. Searched {stats.nodes_expanded} states."
    )
