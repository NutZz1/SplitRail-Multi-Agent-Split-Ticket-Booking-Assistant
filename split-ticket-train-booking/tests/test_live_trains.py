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


def test_error_responses_raise():
    for body in ["~~~~~From station not found", "~~~~~To station not found",
                 "~~~~~Please try again after some time."]:
        with pytest.raises(LiveLookupError):
            parse_between_stations(body)


def test_no_direct_trains_is_an_answer_not_an_error():
    """erail saying "no direct trains" means the pair has no through service.

    It must NOT raise: raising makes web.py treat the provider as broken and
    fall back to the 2020 snapshot, which would then list trains that no
    longer run. RailRadar returns an empty list here for the same reason.
    """
    assert parse_between_stations("~~~~~No direct trains found") == []


def test_empty_response_raises():
    with pytest.raises(LiveLookupError):
        parse_between_stations("")


@pytest.mark.skipif(os.environ.get("SPLITRAIL_LIVE_TESTS") != "1",
                    reason="set SPLITRAIL_LIVE_TESTS=1 to call erail.in")
def test_live_lookup_reaches_erail():
    trains = fetch_between_stations("SBC", "MAS")
    assert trains and all(t.number.isdigit() for t in trains)


# --- narrowing to the stations actually asked for --------------------------
NDLS_BCT = (Path(__file__).parent / "fixtures" / "erail_ndls_bct.txt").read_text()
PURI_CDG = (Path(__file__).parent / "fixtures" / "erail_puri_cdg.txt").read_text()


def test_unfiltered_results_include_other_city_stations():
    """Without narrowing, erail answers for the whole metropolitan area."""
    everything = parse_between_stations(NDLS_BCT)
    assert len(everything) > 30
    assert {t.from_code for t in everything} > {"NDLS"}   # NZM, DEE, DEC too


def test_narrowing_keeps_only_the_requested_pair():
    """NDLS -> BCT must not return trains from Nizamuddin or into Bandra."""
    narrowed = parse_between_stations(NDLS_BCT, "NDLS", "BCT")
    assert [t.number for t in narrowed] == ["12952"]


def test_narrowing_follows_a_renamed_station():
    """BCT was renamed MMCT. Matching on the resolved name still finds it.

    A plain code comparison would return nothing here, because no current
    record carries the code BCT any more.
    """
    train = parse_between_stations(NDLS_BCT, "NDLS", "BCT")[0]
    assert train.to_code == "MMCT"           # current code
    assert train.to_name == "Mumbai Central"  # the name BCT resolves to


def test_no_direct_train_returns_empty_rather_than_raising():
    """No direct service is an answer. Raising would fall back to 2020 data."""
    assert parse_between_stations(PURI_CDG, "PURI", "CDG") == []


def test_sbc_mas_narrows_to_the_station_asked_for(trains):
    narrowed = parse_between_stations(FIXTURE.read_text(), "SBC", "MAS")
    assert len(narrowed) < len(trains)
    assert all(t.from_code == "SBC" and t.to_code == "MAS" for t in narrowed)
