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


# --------------------------------------------------------------------------- #
# Real proposals for the Stage-2 scoring agents (built in-process, no pool)
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def mail_proposal(store, heuristic, mail_query):
    """SBC -> MAS direct on 12658, exactly what SameTrainSearchAgent returns."""
    from src.search_astar import astar_search
    from tests.proposal_fixtures import proposal_from_goal
    goal, _ = astar_search(mail_query, store, heuristic)
    return proposal_from_goal(goal, mail_query, "SameTrainSearchAgent")


@pytest.fixture(scope="session")
def bnc_split_proposal(store, mail_query):
    """SBC -> MAS on 12658 with a real coach switch at BNC (5-min halt), built from real successors."""
    from tests.proposal_fixtures import ride_with_transfer_at
    return ride_with_transfer_at(store, mail_query, "BNC", board_class="3A")


@pytest.fixture(scope="session")
def puri_cdg_proposal(store, heuristic):
    """PURI -> CDG via the real 12801 -> 12217 connection at NDLS (400-min buffer)."""
    import datetime as dt
    from src.search_astar import astar_search
    from src.state import UserQuery
    from src.successors_different_train import get_successors_different_train
    from tests.proposal_fixtures import proposal_from_goal
    q = UserQuery("PURI", "CDG", dt.date(2026, 9, 15), max_transfers=2)
    goal, _ = astar_search(q, store, heuristic, successors_fn=get_successors_different_train)
    return proposal_from_goal(goal, q, "DifferentTrainSearchAgent")


@pytest.fixture(scope="session")
def no_solution_proposal(store, heuristic):
    """SBC -> MYS: no boardable train reaches MYS."""
    import datetime as dt
    from src.search_astar import astar_search
    from src.state import UserQuery
    from tests.proposal_fixtures import proposal_from_goal
    q = UserQuery("SBC", "MYS", dt.date(2026, 9, 16))
    goal, _ = astar_search(q, store, heuristic)
    return proposal_from_goal(goal, q, "SameTrainSearchAgent", reason="No single train runs SBC -> MYS")
