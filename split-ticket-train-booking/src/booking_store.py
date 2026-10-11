"""Persistent, segment-aware simulated reservations.

Reference timetables stay in railway.db; the versioned deterministic seed in
inventory.py plus ACTIVE rows in bookings.db define physical-seat occupancy.
Seats are keyed by train, origin run date, coach and number; occupied spans
are half-open intervals of real halt indices. Adjacent spans may reuse seats.

BookingService rechecks the timetable, seats and server-calculated fare under
BEGIN IMMEDIATE. Every leg of an itinerary commits together or rolls back.
Cancellation retains history and removes the ACTIVE occupancy overlay.
Legacy count-only rows are preserved and conservatively consume capacity;
their historical physical seat identity remains unknown.
"""

from __future__ import annotations

import datetime as dt
import os
import json
import logging
from contextlib import contextmanager
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids an import cycle
    from src.data_store import RailDataStore

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BOOKINGS_PATH = PROJECT_ROOT / "bookings.db"

#: Environment override for the bookings file. The test suite points this at a
#: throwaway database so a test run never touches real reservations.
BOOKINGS_PATH_ENV = "SPLIT_TICKET_BOOKINGS_DB"


def default_bookings_path() -> Path:
    """Where a store looks for bookings when given no explicit path."""
    return Path(os.environ.get(BOOKINGS_PATH_ENV) or DEFAULT_BOOKINGS_PATH)

#: A booking is either live or cancelled. Cancelled rows are kept so the
#: demo can show that a cancellation frees the berth again.
BOOKING_STATUSES = frozenset({"ACTIVE", "CANCELLED"})

#: Ladder used when a coach fills up. Booking never makes a coach *better*.
_DEGRADE = {"CONFIRMED": "RAC", "RAC": "WAITLIST", "WAITLIST": "WAITLIST"}

#: Below this fraction of a coach's berths remaining, the baseline status is
#: degraded one step (CONFIRMED -> RAC -> WAITLIST). At zero it becomes
#: UNAVAILABLE. 0.10 means the last ~7 berths of a 72-berth sleeper are RAC,
#: which is roughly how a real quota behaves as it runs out.
RAC_BAND: float = 0.10

#: Fallback capacity when a coach's class has no berth rule (should not
#: happen for the demo rakes; kept so a data gap degrades gracefully).
DEFAULT_CAPACITY: int = 72

SCHEMA = """
CREATE TABLE IF NOT EXISTS bookings (
    id              TEXT PRIMARY KEY,
    train_number    TEXT NOT NULL,
    run_date        TEXT NOT NULL,
    from_station    TEXT NOT NULL,
    to_station      TEXT NOT NULL,
    from_order      INTEGER NOT NULL,
    to_order        INTEGER NOT NULL,
    coach_code      TEXT NOT NULL,
    travel_class    TEXT,
    passenger_count INTEGER NOT NULL CHECK (passenger_count >= 1),
    passenger_name  TEXT,
    status          TEXT NOT NULL CHECK (status IN ('ACTIVE', 'CANCELLED')),
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    CHECK (from_order < to_order)
);
CREATE INDEX IF NOT EXISTS idx_bookings_run
    ON bookings (train_number, run_date, status);
CREATE INDEX IF NOT EXISTS idx_bookings_coach
    ON bookings (train_number, run_date, coach_code, status);
"""


class BookingError(ValueError):
    """Invalid booking request; the message is meant for the screen / HTTP 400."""


# --------------------------------------------------------------------------- #
# Row type
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Booking:
    """One reservation: N berths in one coach, on one train, on one date."""

    id: str
    train_number: str
    run_date: str
    from_station: str
    to_station: str
    from_order: int
    to_order: int
    coach_code: str
    travel_class: Optional[str]
    passenger_count: int
    passenger_name: Optional[str]
    status: str
    created_at: str
    updated_at: str
    seat_numbers_json: str = "[]"
    journey_id: str | None = None
    fare: float | None = None
    departure: str | None = None
    arrival: str | None = None

    @property
    def seat_numbers(self) -> tuple[int, ...]:
        return tuple(json.loads(self.seat_numbers_json))

    @property
    def is_active(self) -> bool:
        return self.status == "ACTIVE"

    def overlaps(self, from_order: int, to_order: int) -> bool:
        """True when this booking occupies a berth anywhere inside ``[from_order, to_order)``."""
        return self.from_order < to_order and from_order < self.to_order

    def describe(self) -> str:
        return (
            f"{self.id} {self.train_number} {self.run_date} "
            f"{self.from_station}->{self.to_station} {self.coach_code} "
            f"x{self.passenger_count} [{self.status}]"
        )


# --------------------------------------------------------------------------- #
# Occupancy helpers (pure, so they are testable without a database)
# --------------------------------------------------------------------------- #
def seats_taken(bookings: list[Booking], coach_code: str, from_order: int, to_order: int) -> int:
    """Berths already reserved in ``coach_code`` across ``[from_order, to_order)``."""
    return sum(
        b.passenger_count
        for b in bookings
        if b.is_active and b.coach_code == coach_code and b.overlaps(from_order, to_order)
    )


def effective_status(baseline: str, capacity: int, taken: int) -> str:
    """Baseline availability adjusted for how full the coach already is.

    A coach the generator marked UNAVAILABLE stays UNAVAILABLE -- a booking
    can only ever make a segment worse, never better. Otherwise the status
    degrades one step once the coach is nearly full, and becomes UNAVAILABLE
    when it is full.
    """
    if baseline == "UNAVAILABLE" or capacity <= 0:
        return "UNAVAILABLE"
    remaining = capacity - taken
    if remaining <= 0:
        return "UNAVAILABLE"
    if remaining <= max(1, int(capacity * RAC_BAND)):
        return _DEGRADE.get(baseline, baseline)
    return baseline


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #
class BookingStore:
    """Read-write CRUD over ``bookings.db``.

    Separate from :class:`~src.data_store.RailDataStore` on purpose: that one
    guarantees it never writes, and its file is regenerated whenever the
    booking window moves. Use as a context manager, or call :meth:`close`.
    """

    def __init__(self, path: Union[str, Path, None] = None, *, read_only: bool = False) -> None:
        self.path: Path = Path(path) if path is not None else default_bookings_path()
        self.read_only = read_only
        self._transaction_depth = 0
        # check_same_thread=False for the same reason as RailDataStore: a
        # threaded server serves from worker threads. Access is serialised by
        # the caller (web.py's ENGINE_LOCK), not by sqlite3.
        if read_only:
            if not self.path.is_file():
                raise FileNotFoundError(f"bookings.db not found at {self.path}")
            uri = self.path.resolve().as_uri() + "?mode=ro"
            self._conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.path, check_same_thread=False)
            self._conn.executescript(SCHEMA)
            columns = {r[1] for r in self._conn.execute("PRAGMA table_info(bookings)")}
            if 'seat_numbers_json' not in columns and self._conn.execute('SELECT COUNT(*) FROM bookings').fetchone()[0]:
                backup = self.path.with_suffix(self.path.suffix + '.pre-seats.bak')
                if not backup.exists():
                    self._conn.commit()
                    with sqlite3.connect(backup) as copy:
                        self._conn.backup(copy)
            for name, declaration in {"seat_numbers_json": "TEXT NOT NULL DEFAULT '[]'", "journey_id": "TEXT",
                                      "fare": "REAL", "departure": "TEXT", "arrival": "TEXT"}.items():
                if name not in columns:
                    self._conn.execute(f"ALTER TABLE bookings ADD COLUMN {name} {declaration}")
            self._conn.execute("PRAGMA busy_timeout=10000")
            from src.config import INVENTORY_VERSION, DEFAULT_INVENTORY_PROFILE
            self._conn.execute('CREATE TABLE IF NOT EXISTS simulation_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            version = self._conn.execute("SELECT value FROM simulation_meta WHERE key='inventory_version'").fetchone()
            if version and version[0] not in ('segment-v1', INVENTORY_VERSION):
                raise BookingError('Inventory version changed; an explicit ledger migration is required.')
            self._conn.commit()
            if version and version[0] == 'segment-v1':
                backup = self.path.with_suffix(self.path.suffix + '.pre-busy.bak')
                if not backup.exists():
                    with sqlite3.connect(backup) as copy:
                        self._conn.backup(copy)
            with self.transaction():
                self._conn.execute('CREATE TABLE IF NOT EXISTS inventory_run_profiles '
                                   '(train_number TEXT NOT NULL, run_date TEXT NOT NULL, profile TEXT NOT NULL, '
                                   'PRIMARY KEY (train_number, run_date))')
                if version and version[0] == 'segment-v1':
                    # Preserve the exact original seed for every historically
                    # booked run, including cancelled bookings and later reuse.
                    self._conn.execute("INSERT OR IGNORE INTO inventory_run_profiles "
                                       "SELECT DISTINCT train_number, run_date, 'standard-v1' FROM bookings")
                self._conn.execute("INSERT OR IGNORE INTO simulation_meta VALUES ('default_inventory_profile', ?)",
                                   (DEFAULT_INVENTORY_PROFILE,))
                self._conn.execute("INSERT OR REPLACE INTO simulation_meta VALUES ('inventory_version', ?)", (INVENTORY_VERSION,))
            self._conn.commit()
        self._conn.row_factory = sqlite3.Row

    def inventory_profile(self, train_number, run_date):
        """The persisted seed policy; migration keeps existing booked runs intact."""
        # Old read-only connections can exist briefly before startup migration.
        tables = {r[0] for r in self._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'inventory_run_profiles' not in tables:
            return 'standard-v1'
        row = self._conn.execute('SELECT profile FROM inventory_run_profiles WHERE train_number=? AND run_date=?',
                                 (train_number, str(run_date))).fetchone()
        if row:
            return row[0]
        row = self._conn.execute("SELECT value FROM simulation_meta WHERE key='default_inventory_profile'").fetchone()
        return row[0] if row else 'standard-v1'

    @contextmanager
    def transaction(self):
        """Serialize check-and-allocate across connections, with true rollback."""
        outer = self._transaction_depth == 0
        if outer:
            self._conn.execute("BEGIN IMMEDIATE")
        self._transaction_depth += 1
        try:
            yield
            if outer:
                self._conn.commit()
        except BaseException:
            if outer:
                self._conn.rollback()
            raise
        finally:
            self._transaction_depth -= 1

    def _commit(self):
        if not self._transaction_depth:
            self._conn.commit()

    # -- lifecycle ----------------------------------------------------------
    def clone(self, *, read_only: bool | None = None) -> "BookingStore":
        """A fresh connection to the same file (``sqlite3`` binds one per thread)."""
        return BookingStore(self.path, read_only=self.read_only if read_only is None else read_only)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None  # type: ignore[assignment]

    def __enter__(self) -> "BookingStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"BookingStore({str(self.path)!r}, read_only={self.read_only})"

    # -- CREATE -------------------------------------------------------------
    def create(
        self,
        *,
        train_number: str,
        run_date: str,
        from_station: str,
        to_station: str,
        from_order: int,
        to_order: int,
        coach_code: str,
        travel_class: str | None = None,
        passenger_count: int = 1,
        passenger_name: str | None = None,
        booking_id: str | None = None,
        seat_numbers: tuple[int, ...] = (),
        journey_id: str | None = None,
        fare: float | None = None,
        departure: str | None = None,
        arrival: str | None = None,
    ) -> Booking:
        """Insert one reservation and return it. Validation belongs to
        :class:`BookingService`; this method only enforces the table's own rules."""
        if passenger_count < 1:
            raise BookingError("A booking needs at least one passenger.")
        if from_order >= to_order:
            raise BookingError("The boarding stop must come before the alighting stop.")
        now = dt.datetime.now().isoformat(timespec="seconds")
        row = Booking(
            id=booking_id or _new_id(),
            train_number=train_number,
            run_date=run_date,
            from_station=from_station,
            to_station=to_station,
            from_order=from_order,
            to_order=to_order,
            coach_code=coach_code,
            travel_class=travel_class,
            passenger_count=passenger_count,
            passenger_name=passenger_name,
            status="ACTIVE",
            created_at=now,
            updated_at=now,
        )
        self._conn.execute(
            """
            INSERT INTO bookings (id, train_number, run_date, from_station, to_station,
                                  from_order, to_order, coach_code, travel_class,
                                  passenger_count, passenger_name, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (row.id, row.train_number, row.run_date, row.from_station, row.to_station,
             row.from_order, row.to_order, row.coach_code, row.travel_class,
             row.passenger_count, row.passenger_name, row.status, row.created_at, row.updated_at),
        )
        self._conn.execute(
            "UPDATE bookings SET seat_numbers_json=?, journey_id=?, fare=?, departure=?, arrival=? WHERE id=?",
            (json.dumps(seat_numbers), journey_id, fare, departure, arrival, row.id))
        self._commit()
        return self.get(row.id)

    # -- READ ---------------------------------------------------------------
    def get(self, booking_id: str) -> Optional[Booking]:
        row = self._conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
        return Booking(**dict(row)) if row else None

    def list(
        self,
        *,
        train_number: str | None = None,
        run_date: str | None = None,
        coach_code: str | None = None,
        status: str | None = "ACTIVE",
        limit: int = 500,
    ) -> list[Booking]:
        """Bookings matching every filter given, newest first. ``status=None`` includes cancelled."""
        where, params = [], []
        for column, value in (("train_number", train_number), ("run_date", run_date),
                              ("coach_code", coach_code), ("status", status)):
            if value is not None:
                where.append(f"{column} = ?")
                params.append(value)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        params.append(limit)
        rows = self._conn.execute(
            f"SELECT * FROM bookings {clause} ORDER BY created_at DESC, id LIMIT ?", params
        ).fetchall()
        return [Booking(**dict(r)) for r in rows]

    def active_for_run(self, train_number: str, run_date: str) -> list[Booking]:
        """Every live booking on one train on one date -- the input to the availability merge."""
        rows = self._conn.execute(
            "SELECT * FROM bookings WHERE train_number = ? AND run_date = ? AND status = 'ACTIVE'",
            (train_number, run_date),
        ).fetchall()
        return [Booking(**dict(r)) for r in rows]

    # -- UPDATE -------------------------------------------------------------
    def update(
        self,
        booking_id: str,
        *,
        passenger_count: int | None = None,
        passenger_name: str | None = None,
        coach_code: str | None = None,
        travel_class: str | None = None,
        status: str | None = None,
    ) -> Booking:
        """Change a booking in place and return the updated row."""
        current = self.get(booking_id)
        if current is None:
            raise BookingError(f"No booking with id {booking_id!r}.")
        if passenger_count is not None and passenger_count < 1:
            raise BookingError("A booking needs at least one passenger.")
        if status is not None and status not in BOOKING_STATUSES:
            raise BookingError(f"Status must be one of {', '.join(sorted(BOOKING_STATUSES))}.")

        fields = {
            "passenger_count": passenger_count,
            "passenger_name": passenger_name,
            "coach_code": coach_code,
            "travel_class": travel_class,
            "status": status,
        }
        changes = {k: v for k, v in fields.items() if v is not None}
        if not changes:
            return current
        changes["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
        assignments = ", ".join(f"{k} = ?" for k in changes)
        self._conn.execute(
            f"UPDATE bookings SET {assignments} WHERE id = ?", [*changes.values(), booking_id]
        )
        self._commit()
        updated = self.get(booking_id)
        assert updated is not None
        return updated

    # -- DELETE -------------------------------------------------------------
    def cancel(self, booking_id: str) -> Booking:
        """Soft delete: the berth is freed but the row is kept for the audit trail."""
        return self.update(booking_id, status="CANCELLED")

    def delete(self, booking_id: str) -> bool:
        """Hard delete. Returns False if there was nothing to delete."""
        cursor = self._conn.execute("DELETE FROM bookings WHERE id = ?", (booking_id,))
        self._commit()
        return cursor.rowcount > 0

    def clear(self) -> int:
        """Delete every booking. For demo resets and test teardown."""
        cursor = self._conn.execute("DELETE FROM bookings")
        self._commit()
        return cursor.rowcount


def _new_id() -> str:
    """A short, PNR-looking identifier."""
    return uuid.uuid4().hex[:10].upper()


# --------------------------------------------------------------------------- #
# Service: validation that needs the timetable
# --------------------------------------------------------------------------- #
class BookingService:
    """Booking with full validation against ``railway.db``.

    Everything a caller can get wrong is checked here and reported as
    :class:`BookingError`, so the API and CLI both refuse the same things:
    unknown train, a date outside the rolling window or one the train does
    not run, stations that are not real halts or are in the wrong order, a
    coach that is not in the rake, and a request for more berths than the
    coach has left.
    """

    def __init__(self, store: "RailDataStore", bookings: BookingStore) -> None:
        self._store = store
        self._bookings = bookings

    def book(self, train_number: str, run_date: str, from_station: str, to_station: str,
             coach_code: str, passenger_count: int = 1, passenger_name: str | None = None,
             seat_numbers=None, journey_id=None) -> Booking:
        from src.inventory import InventoryService
        from src.constraint_checks import stop_datetime
        from src.state import Ticket
        from src.fare_model import compute_fare
        train_number, coach_code = str(train_number).strip(), str(coach_code).strip().upper()
        from_station, to_station = from_station.strip().upper(), to_station.strip().upper()
        if type(passenger_count) is not int or not 1 <= passenger_count <= 200:
            raise BookingError("A booking needs between 1 and 200 passengers.")
        if seat_numbers is not None and (not isinstance(seat_numbers, (list, tuple))
                                        or any(type(n) is not int for n in seat_numbers)):
            raise BookingError("Seat numbers must be a list of integers.")
        self._check_runs(train_number, run_date)
        a, b = self._stop_orders(train_number, from_station, to_station)
        composition = self._store.get_coach_composition(train_number)
        if coach_code not in composition or not self._store.coach_travel_class(coach_code):
            raise BookingError(f"Coach {coach_code} is not in train {train_number}'s reservable rake.")
        stops = {s.station_code: s for s in self._store.get_real_halt_stops(train_number)}
        date = dt.date.fromisoformat(run_date)
        departure = stop_datetime(stops[from_station], date, use_arrival=False)
        arrival = stop_datetime(stops[to_station], date, use_arrival=True)
        window = self._store.get_window()
        if departure is None or arrival is None or arrival <= departure:
            raise BookingError("Timetable data unavailable for this segment.")
        from src.rail_graph import segment_minutes
        route = [s for s in stops.values() if a <= s.stop_order <= b]
        if any((segment_minutes(left, right) or 0) <= 0 for left, right in zip(route, route[1:])):
            raise BookingError("Timetable data is inconsistent along this segment.")
        if window and not window[0] <= departure.date().isoformat() <= window[1]:
            # Later connection legs are allowed within the configured journey
            # horizon; book_itinerary validates the first boarding date.
            from src.config import MAX_JOURNEY_DAYS
            if journey_id is None or not window[0] <= departure.date().isoformat() <= (
                    dt.date.fromisoformat(window[1]) + dt.timedelta(days=MAX_JOURNEY_DAYS)).isoformat():
                raise BookingError("Boarding date is outside the booking window.")
        with self._bookings.transaction():
            inventory = InventoryService(self._store, self._bookings)
            free = inventory.available(train_number, run_date, from_station, to_station, coach_code)
            selected = tuple(free[:passenger_count]) if seat_numbers is None else tuple(seat_numbers)
            if (len(selected) != passenger_count or len(set(selected)) != len(selected)
                    or any(type(n) is not int for n in selected)):
                raise BookingError(f"Only {len(free)} berth(s) left; supply one distinct valid seat per passenger.")
            valid = {s.number for s in inventory.seats(train_number, coach_code)}
            if any(n not in valid for n in selected):
                raise BookingError("Invalid seat number for this coach.")
            if any(n not in free for n in selected):
                raise BookingError("Seat became unavailable on an overlapping segment. Search again.")
            ticket = Ticket(train_number, from_station, to_station, coach_code,
                            self._store.coach_travel_class(coach_code), departure, arrival,
                            'CONFIRMED', selected, run_date)
            fare = compute_fare(ticket, self._store, passenger_count)
            if fare is None:
                raise BookingError("Fare data unavailable for this segment.")
            booking = self._bookings.create(
                train_number=train_number, run_date=run_date, from_station=from_station, to_station=to_station,
                from_order=a, to_order=b, coach_code=coach_code, travel_class=ticket.travel_class,
                passenger_count=passenger_count, passenger_name=passenger_name,
                seat_numbers=selected, journey_id=journey_id,
                fare=fare,
                departure=departure.isoformat(), arrival=arrival.isoformat())
        if journey_id is None:
            logging.getLogger(__name__).info("booking_success train=%s legs=1 passengers=%d", train_number, passenger_count)
        return booking

    def book_itinerary(self, legs, *, passenger_count=1, passenger_name=None):
        from src.config import MIN_SEAT_CHANGE, MIN_TRAIN_CHANGE, MAX_JOURNEY_DAYS
        if not isinstance(legs, list) or not 1 <= len(legs) <= 3 or any(not isinstance(leg, dict) for leg in legs):
            raise BookingError("An itinerary needs between one and three structured legs.")
        journey_id = 'SIM-' + uuid.uuid4().hex[:12].upper()
        made = []
        with self._bookings.transaction():
            for leg in legs:
                booking = self.book(str(leg.get('train_number', '')), str(leg.get('train_run_date', leg.get('run_date', ''))),
                                    str(leg.get('from_station', '')), str(leg.get('to_station', '')),
                                    str(leg.get('coach', leg.get('coach_code', ''))),
                                    passenger_count, passenger_name, leg.get('seat_numbers'), journey_id)
                if not made:
                    window = self._store.get_window()
                    if window and not window[0] <= booking.departure[:10] <= window[1]:
                        raise BookingError("Boarding date is outside the booking window.")
                else:
                    previous = made[-1]
                    if previous.to_station != booking.from_station:
                        raise BookingError("Itinerary legs do not connect at the same station.")
                    same = (previous.train_number, previous.run_date) == (booking.train_number, booking.run_date)
                    buffer = (dt.datetime.fromisoformat(booking.departure) - dt.datetime.fromisoformat(previous.arrival)).total_seconds()/60
                    if buffer < (MIN_SEAT_CHANGE if same else MIN_TRAIN_CHANGE):
                        raise BookingError("Insufficient time for the requested transfer.")
                    if same and previous.coach_code == booking.coach_code and previous.seat_numbers == booking.seat_numbers:
                        raise BookingError("A seat-change itinerary must change seats.")
                if (dt.datetime.fromisoformat(booking.arrival) - dt.datetime.fromisoformat(made[0].departure if made else booking.departure)).days > MAX_JOURNEY_DAYS:
                    raise BookingError("Journey exceeds the supported duration.")
                made.append(booking)
        logging.getLogger(__name__).info("booking_success legs=%d passengers=%d", len(made), passenger_count)
        return made

    def rebook(self, booking_id: str, *, passenger_count: int | None = None, **fields) -> Booking:
        with self._bookings.transaction():
            current = self._bookings.get(booking_id)
            if current is None:
                raise BookingError(f"No booking with id {booking_id!r}.")
            if fields.get('status') == 'CANCELLED':
                return self._bookings.cancel(booking_id)
            if fields.get('status') not in (None, 'ACTIVE'):
                raise BookingError("Status must be ACTIVE or CANCELLED.")
            count = current.passenger_count if passenger_count is None else passenger_count
            self._bookings.cancel(booking_id)
            replacement = self.book(current.train_number, current.run_date, current.from_station, current.to_station,
                                    fields.get('coach_code') or current.coach_code, count,
                                    fields.get('passenger_name') or current.passenger_name,
                                    journey_id=current.journey_id)
            self._bookings._conn.execute(
                "UPDATE bookings SET passenger_count=?, seat_numbers_json=?, fare=?, status='ACTIVE', coach_code=?, travel_class=?, passenger_name=?, updated_at=? WHERE id=?",
                (count, replacement.seat_numbers_json, replacement.fare, replacement.coach_code, replacement.travel_class, replacement.passenger_name, replacement.updated_at, booking_id))
            self._bookings.delete(replacement.id)
            return self._bookings.get(booking_id)

    def seats_remaining(self, train_number, run_date, from_station, to_station, coach_code):
        from src.inventory import InventoryService
        self._stop_orders(train_number, from_station, to_station)
        return len(InventoryService(self._store, self._bookings).available(
            train_number, run_date, from_station, to_station, coach_code))

    # -- internals ----------------------------------------------------------
    def _check_runs(self, train_number: str, run_date: str) -> None:
        try:
            dt.date.fromisoformat(run_date)
        except ValueError:
            raise BookingError(f"'{run_date}' is not a date in YYYY-MM-DD form.") from None
        window = self._store.get_run_date_range()
        if window and not ((dt.date.fromisoformat(window[0]) - dt.timedelta(days=7)).isoformat() <= run_date <= (dt.date.fromisoformat(window[1]) + dt.timedelta(days=4)).isoformat()):
            raise BookingError(
                f"{run_date} is outside the booking window ({window[0]} .. {window[1]})."
            )
        if dt.date.fromisoformat(run_date).strftime("%a").upper() not in self._store.get_run_pattern(train_number):
            raise BookingError(f"Train {train_number} does not run on {run_date}.")

    def _stop_orders(self, train_number: str, from_station: str, to_station: str) -> tuple[int, int]:
        stops = self._store.get_real_halt_stops(train_number)
        order = {s.station_code: s.stop_order for s in stops}
        if from_station not in order:
            raise BookingError(f"Train {train_number} does not halt at {from_station}.")
        if to_station not in order:
            raise BookingError(f"Train {train_number} does not halt at {to_station}.")
        if order[from_station] >= order[to_station]:
            raise BookingError(
                f"Train {train_number} reaches {to_station} before {from_station}; "
                "check the direction of travel."
            )
        return order[from_station], order[to_station]
