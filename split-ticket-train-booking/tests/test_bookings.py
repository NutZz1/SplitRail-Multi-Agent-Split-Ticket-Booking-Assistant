"""
Bookings: CRUD, capacity arithmetic, and the effect on the search.

The point of keying reservations by run date is that booking a coach out on
one day must change that day and leave the rest of the window alone. That
claim is checked here directly, and checked again through BFS, UCS and A* --
all three read availability through the same successor generators, so a
reservation has to be visible to every algorithm without any of them
knowing bookings exist.
"""

from __future__ import annotations

import datetime as dt
import sqlite3

import pytest

from src.booking_store import (
    RAC_BAND,
    Booking,
    BookingError,
    BookingStore,
    effective_status,
    seats_taken,
)
from src.search_astar import astar_search
from src.search_bfs import bfs_search
from src.search_ucs import ucs_search
from src.state import UserQuery

MAIL = "12658"          # SBC -> MAS, daily, in the pinned window
WED = dt.date(2026, 9, 16)
THU = dt.date(2026, 9, 17)


def _booking(**overrides) -> Booking:
    base = dict(
        id="B1", train_number=MAIL, run_date=WED.isoformat(),
        from_station="SBC", to_station="MAS", from_order=0, to_order=10,
        coach_code="B1", travel_class="3A", passenger_count=1,
        passenger_name=None, status="ACTIVE",
        created_at="2026-09-01T00:00:00", updated_at="2026-09-01T00:00:00",
    )
    base.update(overrides)
    return Booking(**base)


# --------------------------------------------------------------------------- #
# Occupancy arithmetic (pure)
# --------------------------------------------------------------------------- #
def test_a_berth_is_occupied_across_every_segment_it_spans():
    """A booking SBC -> MAS blocks the middle of the route, not just the ends."""
    long_haul = _booking(from_order=0, to_order=10, passenger_count=4)
    assert long_haul.overlaps(2, 5) is True      # wholly inside
    assert long_haul.overlaps(0, 1) is True      # at the start
    assert long_haul.overlaps(9, 10) is True     # at the end
    assert long_haul.overlaps(10, 12) is False   # starts where the booking ends
    assert long_haul.overlaps(12, 15) is False   # beyond it


def test_only_active_bookings_in_the_right_coach_count():
    rows = [
        _booking(id="a", coach_code="B1", passenger_count=2),
        _booking(id="b", coach_code="B1", passenger_count=3, status="CANCELLED"),
        _booking(id="c", coach_code="B2", passenger_count=5),
        _booking(id="d", coach_code="B1", passenger_count=7, from_order=20, to_order=25),
    ]
    assert seats_taken(rows, "B1", 0, 10) == 2   # b cancelled, c other coach, d no overlap
    assert seats_taken(rows, "B2", 0, 10) == 5


@pytest.mark.parametrize("taken,expected", [
    (0, "CONFIRMED"),
    (30, "CONFIRMED"),
    (64, "UNAVAILABLE"),   # exactly full
    (99, "UNAVAILABLE"),   # oversubscribed
])
def test_status_degrades_as_a_coach_fills(taken, expected):
    assert effective_status("CONFIRMED", 64, taken) == expected


def test_nearly_full_degrades_one_step():
    capacity = 64
    nearly = capacity - int(capacity * RAC_BAND)  # inside the RAC band, not full
    assert effective_status("CONFIRMED", capacity, nearly) == "RAC"
    assert effective_status("RAC", capacity, nearly) == "WAITLIST"
    assert effective_status("WAITLIST", capacity, nearly) == "WAITLIST"


def test_booking_never_improves_a_segment():
    """An UNAVAILABLE baseline stays UNAVAILABLE however empty the coach is."""
    assert effective_status("UNAVAILABLE", 64, 0) == "UNAVAILABLE"


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #
def test_create_then_read(booking_service, bookings):
    created = booking_service.book(MAIL, WED.isoformat(), "SBC", "MAS", "B1",
                                   passenger_count=2, passenger_name="A Patel")
    assert created.is_active and created.travel_class == "3A"
    assert bookings.get(created.id) == created
    assert [b.id for b in bookings.list(train_number=MAIL)] == [created.id]


def test_list_filters_by_train_date_and_status(booking_service, bookings):
    a = booking_service.book(MAIL, WED.isoformat(), "SBC", "MAS", "B1")
    b = booking_service.book(MAIL, THU.isoformat(), "SBC", "MAS", "B1")
    assert {x.id for x in bookings.list(run_date=WED.isoformat())} == {a.id}
    assert {x.id for x in bookings.list(run_date=THU.isoformat())} == {b.id}
    bookings.cancel(a.id)
    assert {x.id for x in bookings.list(status="ACTIVE")} == {b.id}
    assert {x.id for x in bookings.list(status="CANCELLED")} == {a.id}
    assert {x.id for x in bookings.list(status=None)} == {a.id, b.id}


def test_update_changes_the_booking(booking_service, bookings):
    created = booking_service.book(MAIL, WED.isoformat(), "SBC", "MAS", "B1", passenger_count=2)
    updated = booking_service.rebook(created.id, passenger_count=5, passenger_name="Renamed")
    assert updated.passenger_count == 5 and updated.passenger_name == "Renamed"
    assert updated.id == created.id
    assert updated.updated_at >= created.updated_at


def test_cancel_is_soft_and_frees_the_berths(booking_service, bookings, store):
    capacity = store.coach_capacity(MAIL, "B1")
    created = booking_service.book(MAIL, WED.isoformat(), "SBC", "MAS", "B1",
                                   passenger_count=capacity)
    assert booking_service.seats_remaining(MAIL, WED.isoformat(), "SBC", "MAS", "B1") == 0
    bookings.cancel(created.id)
    assert booking_service.seats_remaining(MAIL, WED.isoformat(), "SBC", "MAS", "B1") == capacity
    assert bookings.get(created.id).status == "CANCELLED"  # row kept for the audit trail


def test_hard_delete_removes_the_row(booking_service, bookings):
    created = booking_service.book(MAIL, WED.isoformat(), "SBC", "MAS", "B1")
    assert bookings.delete(created.id) is True
    assert bookings.get(created.id) is None
    assert bookings.delete(created.id) is False  # nothing left to delete


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("kwargs,message", [
    (dict(run_date="2026-09-30"), "outside the booking window"),
    (dict(run_date="not-a-date"), "not a date"),
    (dict(from_station="MAS", to_station="SBC"), "before"),          # wrong direction
    (dict(from_station="ZZZZ"), "does not halt"),
    (dict(coach_code="Z9"), "not in train"),
    (dict(passenger_count=0), "at least one passenger"),
])
def test_invalid_bookings_are_refused(booking_service, kwargs, message):
    call = dict(train_number=MAIL, run_date=WED.isoformat(), from_station="SBC",
                to_station="MAS", coach_code="B1", passenger_count=1)
    call.update(kwargs)
    with pytest.raises(BookingError, match=message):
        booking_service.book(**call)


def test_cannot_book_a_train_that_does_not_run_that_day(booking_service):
    """12217 runs Tue/Fri; 2026-09-16 is a Wednesday."""
    with pytest.raises(BookingError, match="does not run"):
        booking_service.book("12217", WED.isoformat(), "PURI", "CDG", "B1")


def test_cannot_oversell_a_coach(booking_service, store):
    capacity = store.coach_capacity(MAIL, "B1")
    booking_service.book(MAIL, WED.isoformat(), "SBC", "MAS", "B1", passenger_count=capacity - 1)
    with pytest.raises(BookingError, match="Only 1 berth"):
        booking_service.book(MAIL, WED.isoformat(), "SBC", "MAS", "B1", passenger_count=2)


def test_growing_a_booking_rechecks_capacity(booking_service, store):
    capacity = store.coach_capacity(MAIL, "B1")
    created = booking_service.book(MAIL, WED.isoformat(), "SBC", "MAS", "B1", passenger_count=1)
    with pytest.raises(BookingError, match="more berth"):
        booking_service.rebook(created.id, passenger_count=capacity + 10)


# --------------------------------------------------------------------------- #
# Per-date isolation -- the reason bookings carry a run_date
# --------------------------------------------------------------------------- #
def test_availability_changes_only_on_the_booked_date(booking_service, store):
    capacity = store.coach_capacity(MAIL, "B1")
    before_wed = store.get_availability(MAIL, "SBC", "MAS", WED.isoformat())["B1"]
    before_thu = store.get_availability(MAIL, "SBC", "MAS", THU.isoformat())["B1"]

    booking_service.book(MAIL, WED.isoformat(), "SBC", "MAS", "B1", passenger_count=capacity)

    assert store.get_availability(MAIL, "SBC", "MAS", WED.isoformat())["B1"] == "UNAVAILABLE"
    assert store.get_availability(MAIL, "SBC", "MAS", THU.isoformat())["B1"] == before_thu
    assert before_wed != "UNAVAILABLE"


def test_the_baseline_is_untouched_when_no_date_is_given(booking_service, store):
    """Omitting run_date returns railway.db's date-less baseline, unchanged."""
    capacity = store.coach_capacity(MAIL, "B1")
    baseline = store.get_availability(MAIL, "SBC", "MAS")["B1"]
    booking_service.book(MAIL, WED.isoformat(), "SBC", "MAS", "B1", passenger_count=capacity)
    assert store.get_availability(MAIL, "SBC", "MAS")["B1"] == baseline


def test_a_booking_blocks_the_segments_it_spans(booking_service, store):
    """Booked SBC -> MAS, so an intermediate leg in that coach is gone too."""
    capacity = store.coach_capacity(MAIL, "B1")
    booking_service.book(MAIL, WED.isoformat(), "SBC", "MAS", "B1", passenger_count=capacity)
    leg = store.get_availability(MAIL, "SBC", "BNC", WED.isoformat())
    assert leg["B1"] == "UNAVAILABLE"


def test_get_availability_from_applies_the_same_merge(booking_service, store):
    capacity = store.coach_capacity(MAIL, "B1")
    booking_service.book(MAIL, WED.isoformat(), "SBC", "MAS", "B1", passenger_count=capacity)
    by_destination = store.get_availability_from(MAIL, "SBC", WED.isoformat())
    assert by_destination["MAS"]["B1"] == "UNAVAILABLE"
    untouched = store.get_availability_from(MAIL, "SBC", THU.isoformat())
    assert untouched["MAS"]["B1"] != "UNAVAILABLE"


# --------------------------------------------------------------------------- #
# The search sees bookings -- all three algorithms
# --------------------------------------------------------------------------- #
def _coach_used(goal) -> str | None:
    return goal.tickets_so_far[0].coach if goal and goal.tickets_so_far else None


def _book_out(service, store, coach: str, date: dt.date) -> None:
    service.book(MAIL, date.isoformat(), "SBC", "MAS", coach,
                 passenger_count=store.coach_capacity(MAIL, coach))


@pytest.mark.parametrize("search", [
    pytest.param(lambda q, s, h: astar_search(q, s, h)[0], id="astar"),
    pytest.param(lambda q, s, h: ucs_search(q, s)[0], id="ucs"),
    pytest.param(lambda q, s, h: bfs_search(q, s)[0], id="bfs"),
])
def test_every_algorithm_avoids_a_booked_out_coach(search, booking_service, store, heuristic):
    query = UserQuery("SBC", "MAS", WED, max_transfers=2)
    before = search(query, store, heuristic)
    coach = _coach_used(before)
    assert coach is not None, "expected an itinerary before booking"

    _book_out(booking_service, store, coach, WED)

    after = search(query, store, heuristic)
    assert after is not None, "a full coach must not remove the whole itinerary"
    assert _coach_used(after) != coach


def test_booking_one_date_does_not_change_another(booking_service, store, heuristic):
    wednesday = UserQuery("SBC", "MAS", WED, max_transfers=2)
    thursday = UserQuery("SBC", "MAS", THU, max_transfers=2)
    coach = _coach_used(astar_search(wednesday, store, heuristic)[0])
    _book_out(booking_service, store, coach, WED)

    assert _coach_used(astar_search(wednesday, store, heuristic)[0]) != coach
    assert _coach_used(astar_search(thursday, store, heuristic)[0]) == coach


def test_cancelling_restores_the_original_itinerary(booking_service, bookings, store, heuristic):
    query = UserQuery("SBC", "MAS", WED, max_transfers=2)
    original = _coach_used(astar_search(query, store, heuristic)[0])
    created = booking_service.book(
        MAIL, WED.isoformat(), "SBC", "MAS", original,
        passenger_count=store.coach_capacity(MAIL, original),
    )
    assert _coach_used(astar_search(query, store, heuristic)[0]) != original
    bookings.cancel(created.id)
    assert _coach_used(astar_search(query, store, heuristic)[0]) == original


# --------------------------------------------------------------------------- #
# Isolation from railway.db
# --------------------------------------------------------------------------- #
def test_bookings_live_in_their_own_file(store, bookings):
    """railway.db is rebuilt whenever the window rolls, so it must hold no bookings."""
    assert bookings.path != store.db_path
    assert bookings.path.name == "bookings.db"


def test_a_read_only_bookings_store_refuses_writes(bookings, booking_service, tmp_path):
    booking_service.book(MAIL, WED.isoformat(), "SBC", "MAS", "B1")
    with BookingStore(bookings.path, read_only=True) as reader:
        assert len(reader.list()) == 1
        with pytest.raises(sqlite3.OperationalError):
            reader.create(
                train_number=MAIL, run_date=WED.isoformat(), from_station="SBC",
                to_station="MAS", from_order=0, to_order=10, coach_code="B2",
            )
