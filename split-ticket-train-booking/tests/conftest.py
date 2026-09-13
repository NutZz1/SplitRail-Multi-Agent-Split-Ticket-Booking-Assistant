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
