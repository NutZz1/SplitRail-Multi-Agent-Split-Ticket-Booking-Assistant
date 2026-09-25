"""
Bookings: the read-write half of the data layer.

``railway.db`` is reference data -- timetables, coach layouts, and the
synthetic *baseline* seat availability. It is opened ``mode=ro&immutable=1``
and is rebuilt from scratch whenever the booking window rolls forward, so
nothing that must survive can live in it.

Bookings therefore get their own file, ``bookings.db``, with a read-write
connection and full CRUD:

    create  -- reserve N berths in one coach on one train, on ONE run date
    read    -- get() / list(), by id, train, date, or coach
    update  -- change the passenger count, name, or coach of a booking
    delete  -- cancel() (soft, keeps the audit row) or delete() (hard)

Why bookings are per run date
-----------------------------
The baseline availability table has no date column: it describes a
*typical* run of a train. A reservation is not typical -- it is one berth on
one train on one date. Keying bookings by ``run_date`` is what makes the
rolling window behave the way a real system does: booking out a coach on
day 3 changes day 3 only, and days 4-10 are untouched. The search
algorithms (BFS, UCS, A*) all read availability through
:meth:`~src.data_store.RailDataStore.get_availability_from`, so once that
call is given a date, every algorithm sees bookings without any change to
its own logic.

Occupancy arithmetic
--------------------
A berth booked PURI -> NDLS is occupied for every segment in between, so a
booking blocks a queried segment when the two *overlap* on the train's stop
order, not only when they are identical. Half-open intervals:
``[a, b)`` overlaps ``[c, d)`` iff ``a < d and c < b``.

Capacity comes from ``berth_layout_rules`` (real, published coach specs),
so an SL coach holds 72 and a 1A coach 18. Effective status is the baseline
status degraded by how full the coach is -- see :func:`effective_status`.
"""

from __future__ import annotations

import datetime as dt
import os
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
            self._conn.commit()
        self._conn.row_factory = sqlite3.Row

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
        self._conn.commit()
        return row

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
        self._conn.commit()
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
        self._conn.commit()
        return cursor.rowcount > 0

    def clear(self) -> int:
        """Delete every booking. For demo resets and test teardown."""
        cursor = self._conn.execute("DELETE FROM bookings")
        self._conn.commit()
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

    # -- the write path -----------------------------------------------------
    def book(
        self,
        train_number: str,
        run_date: str,
        from_station: str,
        to_station: str,
        coach_code: str,
        passenger_count: int = 1,
        passenger_name: str | None = None,
    ) -> Booking:
        train_number, coach_code = train_number.strip(), coach_code.strip().upper()
        from_station, to_station = from_station.strip().upper(), to_station.strip().upper()
        if passenger_count < 1:
            raise BookingError("A booking needs at least one passenger.")

        self._check_runs(train_number, run_date)
        from_order, to_order = self._stop_orders(train_number, from_station, to_station)

        composition = self._store.get_coach_composition(train_number)
        if composition and coach_code not in composition:
            raise BookingError(
                f"Coach {coach_code} is not in train {train_number}'s rake "
                f"({', '.join(composition)})."
            )

        baseline = self._store.get_availability(train_number, from_station, to_station)
        if coach_code not in baseline:
            raise BookingError(
                f"Train {train_number} has no availability data for {from_station} -> "
                f"{to_station} in coach {coach_code}."
            )

        remaining = self.seats_remaining(train_number, run_date, from_station, to_station, coach_code)
        if passenger_count > remaining:
            raise BookingError(
                f"Only {remaining} berth(s) left in coach {coach_code} on {train_number} "
                f"{from_station} -> {to_station} on {run_date}; {passenger_count} requested."
            )

        return self._bookings.create(
            train_number=train_number,
            run_date=run_date,
            from_station=from_station,
            to_station=to_station,
            from_order=from_order,
            to_order=to_order,
            coach_code=coach_code,
            travel_class=self._store.coach_travel_class(coach_code),
            passenger_count=passenger_count,
            passenger_name=passenger_name,
        )

    def rebook(self, booking_id: str, *, passenger_count: int | None = None, **fields) -> Booking:
        """Update a booking, re-checking capacity when the passenger count grows."""
        current = self._bookings.get(booking_id)
        if current is None:
            raise BookingError(f"No booking with id {booking_id!r}.")
        if passenger_count is not None and passenger_count > current.passenger_count:
            extra = passenger_count - current.passenger_count
            remaining = self.seats_remaining(
                current.train_number, current.run_date,
                current.from_station, current.to_station, current.coach_code,
            )
            if extra > remaining:
                raise BookingError(
                    f"Only {remaining} more berth(s) available in coach {current.coach_code}; "
                    f"{extra} more requested."
                )
        return self._bookings.update(booking_id, passenger_count=passenger_count, **fields)

    # -- the read path ------------------------------------------------------
    def seats_remaining(
        self, train_number: str, run_date: str,
        from_station: str, to_station: str, coach_code: str,
    ) -> int:
        """Berths left in one coach across one segment on one date."""
        from_order, to_order = self._stop_orders(train_number, from_station, to_station)
        capacity = self._store.coach_capacity(train_number, coach_code)
        taken = seats_taken(
            self._bookings.active_for_run(train_number, run_date), coach_code, from_order, to_order
        )
        return max(capacity - taken, 0)

    # -- internals ----------------------------------------------------------
    def _check_runs(self, train_number: str, run_date: str) -> None:
        try:
            dt.date.fromisoformat(run_date)
        except ValueError:
            raise BookingError(f"'{run_date}' is not a date in YYYY-MM-DD form.") from None
        window = self._store.get_run_date_range()
        if window and not (window[0] <= run_date <= window[1]):
            raise BookingError(
                f"{run_date} is outside the booking window ({window[0]} .. {window[1]})."
            )
        if not self._store.runs_on_date(train_number, run_date):
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
