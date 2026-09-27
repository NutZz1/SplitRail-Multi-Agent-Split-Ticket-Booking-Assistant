"""
The booking endpoints behind the browser interface.

These exercise ``web.py``'s adapter functions directly -- the same functions
the HTTP handler calls -- so the request/response contract the interface
relies on is covered without standing up a socket.

The behaviour that matters is per-date isolation: a reservation changes the
availability, the berth counts and the ranked options for the date it was
made on, and leaves every other date in the window exactly as it was.
"""

from __future__ import annotations

import json

import pytest

from cli import build_system
from src.booking_store import BookingError
from web import (
    availability,
    book_itinerary,
    cancel_booking,
    create_booking,
    list_bookings,
    search,
    update_booking,
)

MAIL = "12658"
WED = "2026-09-16"   # inside the pinned test window (see tests/conftest.py)
THU = "2026-09-17"

SEARCH_WED = {"origin": "SBC", "destination": "MAS", "date": WED,
              "travel_class": "", "max_transfers": 2}


@pytest.fixture(scope="module")
def web_system():
    system = build_system(use_pool=False)
    yield system
    system.close()


@pytest.fixture(autouse=True)
def _clean(web_system):
    """Each test starts from an empty ledger (the module-scoped system is shared)."""
    web_system.bookings.clear()
    yield
    web_system.bookings.clear()


def _leg(system, date=WED):
    """The first leg of the best option for ``date``, as the interface sends it."""
    option = search(system, {**SEARCH_WED, "date": date})["options"][0]
    t = option["tickets"][0]
    return {"train_number": t["train_number"], "from_station": t["from_station"],
            "to_station": t["to_station"], "coach": t["coach"]}


def _seats(system, coach, date=WED):
    return availability(system, {"train_number": [MAIL], "run_date": [date],
                                 "from_station": ["SBC"], "to_station": ["MAS"]})["seats_remaining"][coach]


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #
def test_create_read_update_cancel(web_system):
    created = create_booking(web_system, {
        "train_number": MAIL, "run_date": WED, "from_station": "SBC",
        "to_station": "MAS", "coach_code": "B1", "passenger_count": 2,
        "passenger_name": "A Patel"})["booking"]
    assert created["status"] == "ACTIVE" and created["travel_class"] == "3A"

    listed = list_bookings(web_system, {})["bookings"]
    assert [b["id"] for b in listed] == [created["id"]]

    updated = update_booking(web_system, created["id"], {"passenger_count": 4})["booking"]
    assert updated["passenger_count"] == 4

    cancelled = cancel_booking(web_system, created["id"])["booking"]
    assert cancelled["status"] == "CANCELLED"
    assert list_bookings(web_system, {})["bookings"] == []
    assert len(list_bookings(web_system, {"status": [""]})["bookings"]) == 1  # audit row kept


def test_cancelling_an_unknown_booking_is_a_key_error(web_system):
    """The handler turns KeyError into a 404 rather than a 500."""
    with pytest.raises(KeyError):
        cancel_booking(web_system, "NOSUCHID")


@pytest.mark.parametrize("patch,message", [
    ({"run_date": "2026-09-30"}, "outside the booking window"),
    ({"coach_code": "Z9"}, "not in train"),
    ({"from_station": "MAS", "to_station": "SBC"}, "before"),
    ({"train_number": "12217"}, "does not run"),
])
def test_invalid_bookings_are_rejected(web_system, patch, message):
    body = {"train_number": MAIL, "run_date": WED, "from_station": "SBC",
            "to_station": "MAS", "coach_code": "B1", "passenger_count": 1, **patch}
    with pytest.raises(BookingError, match=message):
        create_booking(web_system, body)


@pytest.mark.parametrize("count", [0, -1, 999, "two", None])
def test_passenger_count_is_validated_at_the_boundary(web_system, count):
    body = {"train_number": MAIL, "run_date": WED, "from_station": "SBC",
            "to_station": "MAS", "coach_code": "B1", "passenger_count": count}
    with pytest.raises(ValueError):
        create_booking(web_system, body)


# --------------------------------------------------------------------------- #
# Whole-itinerary booking, and its rollback
# --------------------------------------------------------------------------- #
def test_book_itinerary_reserves_every_leg(web_system):
    legs = [_leg(web_system)]
    made = book_itinerary(web_system, {"run_date": WED, "legs": legs})["bookings"]
    assert len(made) == len(legs)
    assert all(b["status"] == "ACTIVE" for b in made)


def test_a_failing_leg_rolls_back_the_earlier_ones(web_system):
    """A half-booked journey is worse than none, so the good legs are undone."""
    good = _leg(web_system)
    bad = {**good, "coach": "Z9"}  # not in the rake
    with pytest.raises(BookingError):
        book_itinerary(web_system, {"run_date": WED, "legs": [good, bad]})
    assert list_bookings(web_system, {})["bookings"] == [], "the first leg should have been cancelled"


@pytest.mark.parametrize("legs", [[], None, "nope", [{}] * 5])
def test_itinerary_shape_is_validated(web_system, legs):
    with pytest.raises(ValueError):
        book_itinerary(web_system, {"run_date": WED, "legs": legs})


# --------------------------------------------------------------------------- #
# Per-date isolation, as the interface shows it
# --------------------------------------------------------------------------- #
def test_booking_moves_the_berth_count_on_that_date_only(web_system):
    before_wed, before_thu = _seats(web_system, "B1"), _seats(web_system, "B1", THU)
    create_booking(web_system, {"train_number": MAIL, "run_date": WED, "from_station": "SBC",
                                "to_station": "MAS", "coach_code": "B1", "passenger_count": 3})
    assert _seats(web_system, "B1") == before_wed - 3
    assert _seats(web_system, "B1", THU) == before_thu


def test_search_reports_berths_and_reflects_a_booking(web_system):
    option = search(web_system, SEARCH_WED)["options"][0]
    ticket = option["tickets"][0]
    assert ticket["seats_remaining"] == ticket["seats_total"]

    create_booking(web_system, {"train_number": ticket["train_number"], "run_date": WED,
                                "from_station": ticket["from_station"], "to_station": ticket["to_station"],
                                "coach_code": ticket["coach"], "passenger_count": 5})

    after = search(web_system, SEARCH_WED)["options"][0]["tickets"][0]
    assert after["seats_remaining"] == ticket["seats_total"] - 5
    other_day = search(web_system, {**SEARCH_WED, "date": THU})["options"][0]["tickets"][0]
    assert other_day["seats_remaining"] == other_day["seats_total"]


def test_booking_out_a_coach_changes_the_ranked_options(web_system):
    """The search must route around a full coach -- and only on that date."""
    leg = _leg(web_system)
    total = search(web_system, SEARCH_WED)["options"][0]["tickets"][0]["seats_total"]
    create_booking(web_system, {"train_number": leg["train_number"], "run_date": WED,
                                "from_station": leg["from_station"], "to_station": leg["to_station"],
                                "coach_code": leg["coach"], "passenger_count": total})

    after = search(web_system, SEARCH_WED)["options"][0]["tickets"][0]["coach"]
    assert after != leg["coach"]
    assert search(web_system, {**SEARCH_WED, "date": THU})["options"][0]["tickets"][0]["coach"] == leg["coach"]


# --------------------------------------------------------------------------- #
# Availability endpoint
# --------------------------------------------------------------------------- #
def test_availability_is_serializable_and_complete(web_system):
    result = availability(web_system, {"train_number": [MAIL], "run_date": [WED],
                                       "from_station": ["sbc"], "to_station": ["mas"]})
    json.dumps(result, default=str, allow_nan=False)
    assert result["from_station"] == "SBC" and result["to_station"] == "MAS"
    assert set(result["coaches"]) == set(result["seats_remaining"])
    assert all(v >= 0 for v in result["seats_remaining"].values())


@pytest.mark.parametrize("params", [
    {},
    {"train_number": [MAIL]},
    {"train_number": [MAIL], "run_date": [WED], "from_station": ["SBC"], "to_station": ["ZZZZ"]},
])
def test_availability_rejects_incomplete_or_unknown_queries(web_system, params):
    with pytest.raises(ValueError):
        availability(web_system, params)
