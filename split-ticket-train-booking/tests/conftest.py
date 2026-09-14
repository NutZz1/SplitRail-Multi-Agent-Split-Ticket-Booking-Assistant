"""Shared fixtures: one read-only store, one graph, one heuristic per test session."""

from __future__ import annotations

import pytest

from src.data_store import RailDataStore
from src.heuristic import RailHeuristic
from src.rail_graph import build_graph


@pytest.fixture(scope="session")
def store():
    with RailDataStore() as s:
        yield s


@pytest.fixture(scope="session")
def graph(store):
    return build_graph(store, verbose=True)


@pytest.fixture(scope="session")
def heuristic(graph):
    return RailHeuristic(graph)


@pytest.fixture(scope="session")
def mail_query():
    """SBC -> MAS on a day 12658 runs; the canonical query for the search tests."""
    import datetime as dt
    from src.state import UserQuery
    return UserQuery(origin_station="SBC", destination_station="MAS", travel_date=dt.date(2026, 9, 16), max_transfers=2)


@pytest.fixture(scope="session")
def pool(store):
    """One process pool for the whole session (each worker builds its own graph once)."""
    from src.agents.worker_pool import SearchWorkerPool
    with SearchWorkerPool(store.db_path, max_workers=2) as p:
        p.warm_up()
        yield p
