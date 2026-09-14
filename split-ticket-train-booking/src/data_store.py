"""
Read-only data access layer over railway.db.

Every other component of the split-ticket search system (graph builder,
split-point search, seat-transfer agent, ...) reads railway data through
:class:`RailDataStore` and nothing else. The store never writes to the
database: the SQLite connection is opened in ``mode=ro`` so an accidental
write raises ``sqlite3.OperationalError`` instead of silently mutating the
source of truth.

Usage::

    from src.data_store import RailDataStore

    with RailDataStore() as db:          # defaults to <project root>/railway.db
        stops = db.get_stops("12658")
        halts = db.get_real_halt_stops("12658")
        ok    = db.runs_on_date("12658", "2026-09-16")

Table reference (see README.md / data_source/build_sqlite_db.py):

    stations, trains, schedule_stops, coach_compositions, berth_layout_rules,
    seat_availability, train_run_pattern, train_run_dates
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "railway.db"

#: Valid values for ``seat_availability.status``.
AVAILABILITY_STATUSES = frozenset({"CONFIRMED", "RAC", "WAITLIST", "UNAVAILABLE"})

#: Column name -> human-readable class label, in the order stored on ``trains``.
CLASS_COLUMNS = {
    "first_ac": "1A",
    "second_ac": "2A",
    "third_ac": "3A",
    "sleeper": "SL",
    "chair_car": "CC",
    "first_class": "FC",
}


# --------------------------------------------------------------------------- #
# Row types
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Station:
    """One row of ``stations``."""

    code: str
    name: str
    state: Optional[str]
    zone: Optional[str]
    lat: Optional[float]
    lon: Optional[float]

    @property
    def has_coordinates(self) -> bool:
        """True when both lat and lon are present (293 stations lack them)."""
        return self.lat is not None and self.lon is not None


@dataclass(frozen=True)
class Train:
    """One row of ``trains``."""

    number: str
    name: str
    type: Optional[str]
    from_station_code: Optional[str]
    from_station_name: Optional[str]
    to_station_code: Optional[str]
    to_station_name: Optional[str]
    distance_km: Optional[int]
    duration_h: Optional[int]
    duration_m: Optional[int]
    first_ac: bool
    second_ac: bool
    third_ac: bool
    sleeper: bool
    chair_car: bool
    first_class: bool

    @property
    def classes(self) -> list[str]:
        """Class codes this train offers, e.g. ``['1A', '2A', '3A', 'SL']``."""
        return [label for col, label in CLASS_COLUMNS.items() if getattr(self, col)]

    @property
    def duration_minutes(self) -> Optional[int]:
        """Total scheduled duration in minutes, or None if unknown."""
        if self.duration_h is None and self.duration_m is None:
            return None
        return (self.duration_h or 0) * 60 + (self.duration_m or 0)


@dataclass(frozen=True)
class Stop:
    """One row of ``schedule_stops``.

    ``halt_minutes`` is ``None`` when arrival/departure is missing (typically
    the two termini), ``0.0`` for a technical pass-through, and ``> 0`` for a
    real halt where passengers can board/alight.
    """

    train_number: str
    stop_order: int
    station_code: str
    station_name: Optional[str]
    arrival: Optional[str]
    departure: Optional[str]
    journey_day: Optional[int]
    halt_minutes: Optional[float]

    @property
    def is_real_halt(self) -> bool:
        """True only for stops with a positive halt duration."""
        return self.halt_minutes is not None and self.halt_minutes > 0


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #
class RailDataStore:
    """Read-only query interface over ``railway.db``.

    One ``sqlite3`` connection is opened per instance and reused for every
    query. Use as a context manager, or call :meth:`close` when done.

    All queries are parameterized; no SQL is ever built from string formatting.
    """

    def __init__(self, db_path: Union[str, Path, None] = None) -> None:
        """Open a read-only connection to ``db_path`` (default: project railway.db).

        Raises:
            FileNotFoundError: if the database file does not exist.
        """
        self.db_path: Path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
        if not self.db_path.is_file():
            raise FileNotFoundError(f"railway.db not found at {self.db_path}")

        # mode=ro makes SQLite itself refuse writes on this connection.
        uri = self.db_path.resolve().as_uri() + "?mode=ro"
        self._conn: sqlite3.Connection = sqlite3.connect(uri, uri=True)
        self._conn.row_factory = sqlite3.Row

    # -- lifecycle ----------------------------------------------------------
    def clone(self) -> "RailDataStore":
        """Open a fresh read-only connection to the same database file.

        ``sqlite3`` connections are bound to the thread that created them, so
        code that runs queries in a worker thread (e.g. an agent running A*
        via ``asyncio.to_thread``) must use its own connection. Cloning is
        cheap: it only opens a file handle, no data is copied.
        """
        return RailDataStore(self.db_path)

    def close(self) -> None:
        """Close the underlying connection. Safe to call more than once."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None  # type: ignore[assignment]

    def __enter__(self) -> "RailDataStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"RailDataStore({str(self.db_path)!r})"

    # -- schedules ----------------------------------------------------------
    def get_stops(self, train_number: str) -> list[Stop]:
        """Return every scheduled stop for ``train_number`` ordered by ``stop_order``.

        Includes pass-through stops (``halt_minutes == 0``). Returns an empty
        list for an unknown train.
        """
        rows = self._conn.execute(
            """
            SELECT train_number, stop_order, station_code, station_name,
                   arrival, departure, journey_day, halt_minutes
            FROM schedule_stops
            WHERE train_number = ?
            ORDER BY stop_order
            """,
            (train_number,),
        ).fetchall()
        return [Stop(**dict(r)) for r in rows]

    def get_real_halt_stops(self, train_number: str) -> list[Stop]:
        """Return the stops where a passenger can realistically board or alight.

        That is: the origin, the destination, and every intermediate stop with
        ``halt_minutes > 0``. Pure pass-throughs are excluded. These are the
        only sensible split / coach-transfer candidates. Ordered by
        ``stop_order``; empty for an unknown train.
        """
        rows = self._conn.execute(
            """
            SELECT train_number, stop_order, station_code, station_name,
                   arrival, departure, journey_day, halt_minutes
            FROM schedule_stops
            WHERE train_number = ?
              AND (
                    halt_minutes > 0
                 OR stop_order = (SELECT MIN(stop_order) FROM schedule_stops WHERE train_number = ?)
                 OR stop_order = (SELECT MAX(stop_order) FROM schedule_stops WHERE train_number = ?)
              )
            ORDER BY stop_order
            """,
            (train_number, train_number, train_number),
        ).fetchall()
        return [Stop(**dict(r)) for r in rows]

    def get_all_trains_through_station(self, station_code: str) -> list[str]:
        """Return the distinct train numbers whose schedule includes ``station_code``.

        Includes trains that merely pass through without halting; filter with
        :meth:`get_real_halt_stops` if you need bookable stops only. Sorted
        ascending by train number; empty for an unknown station.
        """
        rows = self._conn.execute(
            """
            SELECT DISTINCT train_number
            FROM schedule_stops
            WHERE station_code = ?
            ORDER BY train_number
            """,
            (station_code,),
        ).fetchall()
        return [r["train_number"] for r in rows]

    def get_all_train_numbers(self) -> list[str]:
        """Return every train number that has at least one scheduled stop.

        Sourced from ``schedule_stops`` rather than ``trains`` so the result is
        exactly the set of trains that can contribute edges to the station
        graph. Sorted ascending.
        """
        rows = self._conn.execute(
            "SELECT DISTINCT train_number FROM schedule_stops ORDER BY train_number"
        ).fetchall()
        return [r["train_number"] for r in rows]

    # -- reference data -----------------------------------------------------
    def get_station(self, station_code: str) -> Optional[Station]:
        """Return the :class:`Station` for ``station_code``, or None if unknown."""
        row = self._conn.execute(
            "SELECT code, name, state, zone, lat, lon FROM stations WHERE code = ?",
            (station_code,),
        ).fetchone()
        return Station(**dict(row)) if row else None

    def get_train(self, train_number: str) -> Optional[Train]:
        """Return the :class:`Train` for ``train_number``, or None if unknown.

        Class flags are exposed both as booleans (``train.sleeper``) and as a
        list of codes (``train.classes``).
        """
        row = self._conn.execute(
            """
            SELECT number, name, type, from_station_code, from_station_name,
                   to_station_code, to_station_name, distance_km, duration_h,
                   duration_m, first_ac, second_ac, third_ac, sleeper,
                   chair_car, first_class
            FROM trains
            WHERE number = ?
            """,
            (train_number,),
        ).fetchone()
        if row is None:
            return None
        data = dict(row)
        for col in CLASS_COLUMNS:
            data[col] = bool(data[col])
        return Train(**data)

    # -- coach composition --------------------------------------------------
    def get_coach_composition(self, train_number: str) -> list[str]:
        """Return the physical coach order for ``train_number`` (engine end first).

        Only the hand-collected demo trains have composition data; for any
        other train this returns an empty list.
        """
        rows = self._conn.execute(
            """
            SELECT coach_code
            FROM coach_compositions
            WHERE train_number = ?
            ORDER BY position
            """,
            (train_number,),
        ).fetchall()
        return [r["coach_code"] for r in rows]

    def coach_distance(self, train_number: str, coach_a: str, coach_b: str) -> Optional[int]:
        """Return how many physical positions apart ``coach_a`` and ``coach_b`` are.

        Adjacent coaches are 1 apart; the same coach is 0. Returns ``None`` if
        the train has no composition data or if either coach code is not in
        the rake. Unreserved coach codes that repeat within a rake (``GS``,
        ``GEN``, ``EOG``, ``SLR``) resolve to their first occurrence.
        """
        row = self._conn.execute(
            """
            SELECT
              (SELECT MIN(position) FROM coach_compositions
                WHERE train_number = ? AND coach_code = ?) AS pos_a,
              (SELECT MIN(position) FROM coach_compositions
                WHERE train_number = ? AND coach_code = ?) AS pos_b
            """,
            (train_number, coach_a, train_number, coach_b),
        ).fetchone()
        if row["pos_a"] is None or row["pos_b"] is None:
            return None
        return abs(row["pos_a"] - row["pos_b"])

    # -- availability -------------------------------------------------------
    def get_availability(self, train_number: str, from_station: str, to_station: str) -> dict[str, str]:
        """Return ``{coach_code: status}`` for the exact segment ``from_station -> to_station``.

        ``status`` is one of CONFIRMED / RAC / WAITLIST / UNAVAILABLE. Returns
        an empty dict when no data exists for that exact (train, from, to)
        triple — which is the case for non-demo trains, for pass-through
        stations, and for segments given in the wrong direction. Coaches are
        returned in physical rake order.
        """
        rows = self._conn.execute(
            """
            SELECT sa.coach_code, sa.status
            FROM seat_availability AS sa
            LEFT JOIN coach_compositions AS cc
                   ON cc.train_number = sa.train_number
                  AND cc.coach_code   = sa.coach_code
            WHERE sa.train_number = ? AND sa.from_station = ? AND sa.to_station = ?
            ORDER BY cc.position, sa.coach_code
            """,
            (train_number, from_station, to_station),
        ).fetchall()
        return {r["coach_code"]: r["status"] for r in rows}

    # -- run calendar -------------------------------------------------------
    def runs_on_date(self, train_number: str, date_str: str) -> bool:
        """Return True if ``train_number`` runs on ``date_str`` (``YYYY-MM-DD``).

        Backed by ``train_run_dates``, which only covers the 6 demo trains
        over 2026-09-10 .. 2026-09-25. Anything outside that (unknown train,
        date outside the window) returns False.
        """
        row = self._conn.execute(
            "SELECT 1 FROM train_run_dates WHERE train_number = ? AND run_date = ? LIMIT 1",
            (train_number, date_str),
        ).fetchone()
        return row is not None
