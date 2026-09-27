"""
Shared, UI-independent pieces used by BOTH the CLI (cli.py) and the browser
server (web.py): system construction, input validation, the demo-train
listing, and the four scripted demo scenarios. One source of truth.

Nothing here searches or scores; it builds the existing engine and
validates user input at the boundary.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from src.agents.coordinator_agent import CoordinatorAgent
from src.agents.different_train_search_agent import DifferentTrainSearchAgent
from src.agents.fare_time_agent import FareTimeAgent
from src.agents.same_train_search_agent import SameTrainSearchAgent
from src.agents.seat_transfer_agent import SeatTransferAgent
from src.agents.worker_pool import SearchWorkerPool
from src.booking_store import BookingService, BookingStore
from src.data_store import RailDataStore, default_db_path
from src.demo_window import WINDOW_DAYS, weekday_code, window_dates
from src.fare_model import FARE_CLASSES
from src.final_recommendation import FinalRecommendation
from src.heuristic import RailHeuristic
from src.rail_graph import build_graph
from src.state import UserQuery

VALID_CLASSES: tuple[str, ...] = FARE_CLASSES  # 2S, SL, CC, 3A, 2A, EC, 1A


# --------------------------------------------------------------------------- #
# System setup
# --------------------------------------------------------------------------- #
@dataclass
class System:
    store: RailDataStore
    heuristic: RailHeuristic
    coordinator: CoordinatorAgent
    pool: SearchWorkerPool | None
    setup_seconds: float
    bookings: BookingStore | None = None
    booking_service: BookingService | None = None

    def close(self) -> None:
        if self.pool is not None:
            self.pool.shutdown()
        if self.bookings is not None:
            self.bookings.close()
        self.store.close()


def ensure_database(db_path: str | Path | None = None, *, verbose: bool = True) -> Path:
    """Build ``railway.db`` if it is missing, or rebuild it if its window has rolled past.

    The booking window is ``today .. today + WINDOW_DAYS - 1``, so a database
    built yesterday no longer covers the last day of today's window. Rebuilding
    is cheap (a few seconds) and safe: bookings live in a separate file, so
    nothing a user created is lost.
    """
    from data_source.build_sqlite_db import build

    path = Path(db_path) if db_path is not None else default_db_path()
    if not path.is_file():
        if verbose:
            print(f"Building {path.name} from the included demo data ...")
        build(path)
        return path

    with RailDataStore(path) as probe:
        stale = probe.window_is_stale()
        window = probe.get_window()
    if stale:
        if verbose:
            was = f"{window[0]} .. {window[1]}" if window else "an older build"
            print(f"Booking window has moved on (was {was}); rebuilding {path.name} ...")
        build(path)
    return path


def build_system(
    db_path: str | Path | None = None,
    use_pool: bool = True,
    bookings_path: str | Path | None = None,
) -> System:
    """One store, one graph + heuristic (built once), one coordinator with all four agents.

    Also opens the read-write bookings database and the :class:`BookingService`
    that validates reservations against the timetable, so the CLI and the API
    share one CRUD implementation.
    """
    t0 = time.perf_counter()
    # 121 timetable pairs have inconsistent day fields and are skipped with a
    # warning each; the count is available on the graph's build_stats instead.
    logging.getLogger("src.rail_graph").setLevel(logging.ERROR)
    store = RailDataStore(db_path, bookings_path)
    heuristic = RailHeuristic(build_graph(store, verbose=False))
    pool = None
    if use_pool:
        pool = SearchWorkerPool(store.db_path, store.bookings_path, max_workers=2)
        pool.warm_up()
    coordinator = CoordinatorAgent(
        SameTrainSearchAgent(store, heuristic, pool=pool),
        DifferentTrainSearchAgent(store, heuristic, pool=pool),
        SeatTransferAgent(store),
        FareTimeAgent(store),
        store=store,
    )
    bookings = BookingStore(store.bookings_path)
    return System(
        store, heuristic, coordinator, pool, time.perf_counter() - t0,
        bookings=bookings, booking_service=BookingService(store, bookings),
    )


def resolve_timed(system: System, query: UserQuery) -> tuple[FinalRecommendation, float]:
    """Synchronous convenience (CLI): resolve and time it."""
    t0 = time.perf_counter()
    rec = asyncio.run(system.coordinator.resolve(query))
    return rec, time.perf_counter() - t0


# --------------------------------------------------------------------------- #
# Input validation (boundary only)
# --------------------------------------------------------------------------- #
class InputError(ValueError):
    """Invalid user input; the message is meant for the screen / HTTP 400."""


def parse_date(text: str, store: RailDataStore) -> dt.date:
    try:
        date = dt.date.fromisoformat(text.strip())
    except ValueError:
        raise InputError(f"'{text}' is not a date in YYYY-MM-DD form.")
    return validate_date(date, store)


def validate_date(date: dt.date, store: RailDataStore) -> dt.date:
    lo, hi = store.get_run_date_range() or ("?", "?")
    if not (lo <= date.isoformat() <= hi):
        raise InputError(
            f"{date} is outside the {WINDOW_DAYS}-day booking window, which covers {lo} to {hi}."
        )
    return date


def validate_station(code: str, store: RailDataStore) -> str:
    code = code.strip().upper()
    if not code:
        raise InputError("Station code cannot be empty.")
    if store.get_station(code) is None:
        raise InputError(f"'{code}' is not a station code in railway.db (codes look like SBC, MAS, PURI).")
    return code


def parse_class(text: str | None) -> str | None:
    text = (text or "").strip().upper()
    if not text:
        return None
    if text not in VALID_CLASSES:
        raise InputError(f"'{text}' is not a class code. Valid: {', '.join(VALID_CLASSES)} (or blank for no preference).")
    return text


# --------------------------------------------------------------------------- #
# Demo-train discoverability
# --------------------------------------------------------------------------- #
def demo_trains(store: RailDataStore) -> list[dict]:
    """The trains with real availability / run-date coverage, as plain dicts."""
    out = []
    for tn in store.get_demo_train_numbers():
        t = store.get_train(tn)
        days = store.get_run_pattern(tn)
        out.append({
            "number": tn,
            "name": t.name if t else "?",
            "from_station": t.from_station_code if t else "?",
            "from_station_name": (t.from_station_name if t else "?") or "?",
            "to_station": t.to_station_code if t else "?",
            "to_station_name": (t.to_station_name if t else "?") or "?",
            "runs_on": days,
            "runs_daily": len(days) == 7,
        })
    return out


# --------------------------------------------------------------------------- #
# Scripted demo scenarios (what --demo runs and what the UI's buttons run)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DemoScenario:
    id: int
    name: str
    description: str
    query: UserQuery


def weekday_in_window(code: str) -> dt.date:
    """The first date in the current booking window that falls on ``code`` (``MON``..``SUN``).

    The scenarios are defined by *weekday*, not by calendar date: 12217 runs
    Tue/Fri, so "a Tuesday" is what scenario 2 actually needs. A ten-day
    window always contains every weekday, so this never fails -- and the
    scenarios keep working as the window rolls forward, instead of expiring
    with a hard-coded September 2026 date.
    """
    for date in window_dates():
        if weekday_code(date) == code.upper():
            return date
    raise ValueError(f"No {code} falls inside the {WINDOW_DAYS}-day window.")  # unreachable


def demo_scenarios() -> tuple[DemoScenario, ...]:
    """The four scripted scenarios, anchored to the current booking window."""
    tuesday, wednesday = weekday_in_window("TUE"), weekday_in_window("WED")
    return (
        DemoScenario(
            1,
            f"SBC -> MAS on 12658 ({wednesday:%a %d %b}, runs daily)",
            "expect: direct ride AND the BNC coach-switch split, both 340 min; direct ranked first",
            UserQuery("SBC", "MAS", wednesday, max_transfers=2),
        ),
        DemoScenario(
            2,
            f"PURI -> CDG ({tuesday:%a %d %b}: 12801 daily, 12217 Tue/Fri)",
            "expect: 12801 to NDLS, 400-min connection, 12217 on to CDG -- from the different-train agent",
            UserQuery("PURI", "CDG", tuesday, max_transfers=2),
        ),
        DemoScenario(
            3,
            "SBC -> MAS with a HARD chair-car (CC) requirement",
            "expect: no itinerary -- 12658 carries no chair car; both agents explain why",
            UserQuery("SBC", "MAS", wednesday, travel_class_preference="CC", class_is_hard_constraint=True),
        ),
        DemoScenario(
            4,
            f"PURI -> CDG on a Wednesday ({wednesday:%d %b}: 12217 does not run Wed)",
            "expect: 12801 still runs but its only connection does not -- handled, not crashed",
            UserQuery("PURI", "CDG", wednesday, max_transfers=2),
        ),
    )
