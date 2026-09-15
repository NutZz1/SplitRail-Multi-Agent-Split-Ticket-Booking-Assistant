"""
Process pool for running searches truly in parallel.

Why processes and not threads
-----------------------------
A* over journey states is CPU-bound Python. With ``railway.db`` opened
``immutable=1`` (no per-query file locking) nothing in a search releases the
GIL, so two searches in threads do not overlap at all -- measured, they run
*slower* than back-to-back (GIL convoy effect). Two searches in two
processes run at ~0.55-0.8x of back-to-back time.

Each worker process opens its own read-only :class:`RailDataStore` and
builds its own :class:`RailHeuristic` once, in the pool initializer (~1 s).
A search request crosses the process boundary as a :class:`UserQuery` plus
a mode name and k, and comes back as ``(list[JourneyState], SearchStats)`` --
all plain dataclasses, so pickling is cheap. Exceptions raised in a worker
propagate to the awaiting coroutine.

Usage::

    with SearchWorkerPool(store.db_path) as pool:
        same = SameTrainSearchAgent(store, heuristic, pool=pool)
        diff = DifferentTrainSearchAgent(store, heuristic, pool=pool)
        a, b = await asyncio.gather(same.propose(q), diff.propose(q))
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Callable

from src.data_store import RailDataStore
from src.heuristic import RailHeuristic
from src.rail_graph import build_graph
from src.search_astar import astar_search_k
from src.search_stats import SearchStats
from src.state import JourneyState, UserQuery
from src.successors import get_successors
from src.successors_different_train import get_successors_different_train

#: Search modes a worker can run, by name (names cross the process boundary, functions do not).
SEARCH_MODES: dict[str, Callable] = {
    "same_train": get_successors,
    "different_train": get_successors_different_train,
}

# Per-worker-process state, populated by _init_worker.
_WORKER: dict[str, object] = {}


def _init_worker(db_path: str) -> None:
    logging.getLogger("src.rail_graph").setLevel(logging.ERROR)  # 121 known bad-timing warnings
    store = RailDataStore(db_path)
    _WORKER["store"] = store
    _WORKER["heuristic"] = RailHeuristic(build_graph(store, verbose=False))


def _run_search(query: UserQuery, mode: str, k: int) -> tuple[list[JourneyState], SearchStats]:
    store: RailDataStore = _WORKER["store"]  # type: ignore[assignment]
    heuristic: RailHeuristic = _WORKER["heuristic"]  # type: ignore[assignment]
    return astar_search_k(query, store, heuristic, k=k, successors_fn=SEARCH_MODES[mode])


class SearchWorkerPool:
    """A small ProcessPoolExecutor whose workers hold a ready-to-use store + heuristic."""

    def __init__(self, db_path: str | Path, max_workers: int = 2) -> None:
        self.db_path = str(db_path)
        self._pool = ProcessPoolExecutor(
            max_workers=max_workers, initializer=_init_worker, initargs=(self.db_path,)
        )
        self.max_workers = max_workers

    def warm_up(self) -> None:
        """Spawn every worker now (each builds its graph) instead of on first use."""
        futures = [self._pool.submit(_worker_ready) for _ in range(self.max_workers)]
        for f in futures:
            f.result()

    async def search(self, query: UserQuery, mode: str, k: int = 1) -> tuple[list[JourneyState], SearchStats]:
        """Run ``astar_search_k`` with the named successor mode in a worker process."""
        if mode not in SEARCH_MODES:
            raise ValueError(f"unknown search mode {mode!r}; expected one of {sorted(SEARCH_MODES)}")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._pool, _run_search, query, mode, k)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=True)

    def __enter__(self) -> "SearchWorkerPool":
        return self

    def __exit__(self, *exc) -> None:
        self.shutdown()


def _worker_ready() -> bool:
    return "heuristic" in _WORKER
