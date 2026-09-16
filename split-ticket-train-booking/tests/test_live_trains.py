"""Parsing of erail.in responses. Runs offline against a saved response.

Only :func:`test_live_lookup_reaches_erail` touches the network, and it is
skipped unless SPLITRAIL_LIVE_TESTS=1, so the suite stays deterministic.
"""
import datetime as dt
import os
from pathlib import Path

import pytest

from src.live_trains import (LiveLookupError, fetch_between_stations,
                             parse_between_stations)

FIXTURE = Path(__file__).parent / "fixtures" / "erail_sbc_mas.txt"


@pytest.fixture(scope="module")
def trains():
    return parse_between_stations(FIXTURE.read_text())


def test_parses_every_train(trains):
    assert len(trains) == 31


def test_fields_are_populated(trains):
    shatabdi = next(t for t in trains if t.number == "12008")
    assert shatabdi.name == "SHATABDI EXP"
    assert (shatabdi.departure, shatabdi.arrival) == ("16:10", "21:25")
    assert shatabdi.duration_minutes == 5 * 60 + 15


def test_running_days_index_zero_is_monday(trains):
    """12008 runs six days a week except Thursday, which is index 3.

    This pins the day mapping: the AniCrad/indian-rail-api project treats
    index 0 as Wednesday, which would make this train skip Saturday instead.
    """
    shatabdi = next(t for t in trains if t.number == "12008")
    assert shatabdi.running_days == "1110111"
    assert "Thursday" not in shatabdi.running_day_names()
    assert shatabdi.runs_on(dt.date(2026, 9, 16))       # a Wednesday
    assert not shatabdi.runs_on(dt.date(2026, 9, 17))   # a Thursday


def test_runs_on_matches_weekday_for_single_day_train(trains):
    sunday_only = next(t for t in trains if t.number == "22697")
    assert sunday_only.running_day_names() == ["Sunday"]
    assert sunday_only.runs_on(dt.date(2026, 9, 20))
    assert not sunday_only.runs_on(dt.date(2026, 9, 16))


def test_times_are_normalised(trains):
    """erail writes 04.35; nothing downstream should see a dot."""
    assert all(":" in t.departure and "." not in t.departure for t in trains)


def test_nearby_stations_are_reported_as_matched(trains):
    """erail widens a query to the city, so from_code is not always SBC."""
    assert {t.from_code for t in trains} > {"SBC"}


def test_error_responses_raise(  ):
    for body in ["~~~~~No direct trains found", "~~~~~From station not found",
                 "~~~~~To station not found", "~~~~~Please try again after some time."]:
        with pytest.raises(LiveLookupError):
            parse_between_stations(body)


def test_empty_response_raises():
    with pytest.raises(LiveLookupError):
        parse_between_stations("")


@pytest.mark.skipif(os.environ.get("SPLITRAIL_LIVE_TESTS") != "1",
                    reason="set SPLITRAIL_LIVE_TESTS=1 to call erail.in")
def test_live_lookup_reaches_erail():
    trains = fetch_between_stations("SBC", "MAS")
    assert trains and all(t.number.isdigit() for t in trains)
