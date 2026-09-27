"""Shared fixtures: one read-only store, one graph, one heuristic per test session.

Time is pinned here. In production the booking window is ``today .. today+9``
and rolls forward daily (:mod:`src.demo_window`), which is exactly what a
reservation system should do -- and exactly what a test suite must not be
subject to, or every assertion naming a date would rot overnight.

So before anything imports a store, this module pins
``SPLITRAIL_WINDOW_START`` to a fixed Thursday and points
``SPLIT_TICKET_DB`` at a throwaway database built for that window. The
existing tests keep their concrete September 2026 dates and keep meaning what
they meant. The rolling behaviour itself is covered separately, in
``tests/test_demo_window.py``, by moving the pin rather than waiting for the
calendar.

Bookings are redirected the same way, so a test run never touches a real
``bookings.db``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# --- pin the clock BEFORE any store is opened ------------------------------ #
#: The window the suite is written against: 2026-09-10 is a Thursday, so the
#: ten-day window 09-10 .. 09-19 contains every weekday and both of the dates
#: the scenarios use (Tue 09-15 and Wed 09-16).
PINNED_WINDOW_START = "2026-09-10"

_TEST_DB_DIR = Path(__file__).resolve().parent / ".pytest-db"
_TEST_DB_DIR.mkdir(exist_ok=True)

os.environ["SPLITRAIL_WINDOW_START"] = PINNED_WINDOW_START
os.environ["SPLIT_TICKET_DB"] = str(_TEST_DB_DIR / "railway.db")
os.environ["SPLIT_TICKET_BOOKINGS_DB"] = str(_TEST_DB_DIR / "bookings.db")

from src.data_store import RailDataStore  # noqa: E402
from src.demo_scenarios import ensure_database  # noqa: E402
from src.heuristic import RailHeuristic  # noqa: E402
from src.rail_graph import build_graph  # noqa: E402

# Built once per machine and reused; rebuilt only if missing or pinned elsewhere.
ensure_database(os.environ["SPLIT_TICKET_DB"], verbose=False)


@pytest.fixture(scope="session")
def store():
    with RailDataStore() as s:
        yield s


@pytest.fixture(autouse=True)
def _no_leftover_live_cache():
    """Live timetable answers are cached in a module-level TTL cache.

    It is shared for the life of the process, so one test's cached success
    would answer another test's lookup -- including tests that deliberately
    make every provider fail and assert the local fallback. Clear it around
    each test so they stay independent.
    """
    from src.live_cache import live_trains_cache

    live_trains_cache.clear()
    yield
    live_trains_cache.clear()


@pytest.fixture(autouse=True)
def _no_leftover_bookings():
    """Every test starts with an empty bookings table.

    Availability is read through the bookings ledger now, so one test's
    reservation would silently change another test's search results. Clearing
    before each test keeps them independent regardless of execution order.
    """
    from src.booking_store import BookingStore

    with BookingStore() as bookings:
        bookings.clear()
    yield
    with BookingStore() as bookings:
        bookings.clear()


@pytest.fixture
def bookings():
    """A read-write bookings store pointed at the throwaway test database."""
    from src.booking_store import BookingStore

    with BookingStore() as b:
        yield b


@pytest.fixture
def booking_service(store, bookings):
    """Booking CRUD with full validation against the pinned test timetable."""
    from src.booking_store import BookingService

    return BookingService(store, bookings)


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
