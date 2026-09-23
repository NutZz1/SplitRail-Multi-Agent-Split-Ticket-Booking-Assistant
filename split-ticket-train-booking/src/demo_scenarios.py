"""
Shared, UI-independent pieces used by BOTH the CLI (cli.py) and the web API
(api/main.py): system construction, input validation, the demo-train
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
from src.data_store import RailDataStore
from src.fare_model import FARE_PER_KM
from src.final_recommendation import FinalRecommendation
from src.heuristic import RailHeuristic
from src.rail_graph import build_graph
from src.state import UserQuery

VALID_CLASSES: tuple[str, ...] = tuple(FARE_PER_KM)  # SL, 3A, 2A, 1A, CC, EC, 2S


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

    def close(self) -> None:
        if self.pool is not None:
            self.pool.shutdown()
        self.store.close()


def build_system(db_path: str | Path | None = None, use_pool: bool = True) -> System:
    """One store, one graph + heuristic (built once), one coordinator with all four agents."""
    t0 = time.perf_counter()
    # 121 timetable pairs have inconsistent day fields and are skipped with a
    # warning each; the count is available on the graph's build_stats instead.
    logging.getLogger("src.rail_graph").setLevel(logging.ERROR)
    store = RailDataStore(db_path)
    heuristic = RailHeuristic(build_graph(store, verbose=False))
    pool = None
    if use_pool:
        pool = SearchWorkerPool(store.db_path, max_workers=2)
        pool.warm_up()
    coordinator = CoordinatorAgent(
        SameTrainSearchAgent(store, heuristic, pool=pool),
        DifferentTrainSearchAgent(store, heuristic, pool=pool),
        SeatTransferAgent(store),
        FareTimeAgent(store),
        store=store,
    )
    return System(store, heuristic, coordinator, pool, time.perf_counter() - t0)


def resolve_timed(system: System, query: UserQuery) -> tuple[FinalRecommendation, float]:
    """Synchronous convenience (CLI): resolve and time it."""
    t0 = time.perf_counter()
    rec = asyncio.run(system.coordinator.resolve(query))
    return rec, time.perf_counter() - t0


async def resolve_timed_async(system: System, query: UserQuery) -> tuple[FinalRecommendation, float]:
    """Async variant (API): resolve on the caller's running loop and time it."""
    t0 = time.perf_counter()
    rec = await system.coordinator.resolve(query)
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
        raise InputError(f"{date} is outside the demo window: run-date data only covers {lo} to {hi}.")
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


DEMO_SCENARIOS: tuple[DemoScenario, ...] = (
    DemoScenario(
        1,
        "SBC -> MAS on 12658 (Wed 16 Sep, runs daily)",
        "expect: direct ride AND the BNC coach-switch split, both 340 min; direct ranked first",
        UserQuery("SBC", "MAS", dt.date(2026, 9, 16), max_transfers=2),
    ),
    DemoScenario(
        2,
        "PURI -> CDG (Tue 15 Sep: 12801 daily, 12217 Tue/Fri)",
        "expect: 12801 to NDLS, 400-min connection, 12217 on to CDG -- from the different-train agent",
        UserQuery("PURI", "CDG", dt.date(2026, 9, 15), max_transfers=2),
    ),
    DemoScenario(
        3,
        "SBC -> MAS with a HARD chair-car (CC) requirement",
        "expect: no itinerary -- 12658 carries no chair car; both agents explain why",
        UserQuery("SBC", "MAS", dt.date(2026, 9, 16), travel_class_preference="CC", class_is_hard_constraint=True),
    ),
    DemoScenario(
        4,
        "PURI -> CDG on a Wednesday (12217 does not run Wed)",
        "expect: 12801 still runs but its only connection does not -- handled, not crashed",
        UserQuery("PURI", "CDG", dt.date(2026, 9, 16), max_transfers=2),
    ),
)
