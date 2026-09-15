"""
Tests for src.data_store.RailDataStore against the REAL railway.db.

These are integration tests by design: the whole point of the data layer is
to faithfully expose what's in the database, so we check known facts from
the dataset (12658 Bangalore-Chennai Mail halts, coach layouts, run calendar)
rather than mocking sqlite.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import sqlite3

import pytest

from src.data_store import (
    AVAILABILITY_STATUSES,
    DEFAULT_DB_PATH,
    RailDataStore,
    Station,
    Stop,
    Train,
)

MAIL = "12658"  # Bangalore - Chennai Mail, daily, SBC -> MAS
KERALA_SK = "12217"  # Kerala Sampark Kranti, Tue/Fri only
NON_DEMO = "04601"  # a real train with no coach/availability/calendar data
UNKNOWN = "99999"


@pytest.fixture(scope="module")
def db():
    with RailDataStore() as store:
        yield store


# --------------------------------------------------------------------------- #
# lifecycle / safety
# --------------------------------------------------------------------------- #
def test_default_db_path_exists():
    assert DEFAULT_DB_PATH.is_file()


def test_missing_db_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        RailDataStore(tmp_path / "nope.db")


def test_connection_is_read_only(db):
    """The connection is opened with mode=ro; any write must be refused by SQLite."""
    with pytest.raises(sqlite3.OperationalError):
        db._conn.execute("CREATE TABLE should_not_exist (x INTEGER)")
    with pytest.raises(sqlite3.OperationalError):
        db._conn.execute("DELETE FROM train_run_dates")


def test_close_is_idempotent():
    store = RailDataStore()
    store.close()
    store.close()  # second call must not raise


# --------------------------------------------------------------------------- #
# get_stops
# --------------------------------------------------------------------------- #
def test_get_stops_ordered_ascending(db):
    stops = db.get_stops(MAIL)
    assert stops, "12658 must have stops"
    orders = [s.stop_order for s in stops]
    assert orders == sorted(orders)
    assert len(set(orders)) == len(orders), "stop_order must be unique per train"
    assert all(isinstance(s, Stop) and s.train_number == MAIL for s in stops)


def test_get_stops_termini(db):
    stops = db.get_stops(MAIL)
    assert stops[0].station_code == "SBC"
    assert stops[-1].station_code == "MAS"
    assert stops[0].arrival is None and stops[0].departure == "22:45:00"
    assert stops[-1].departure is None and stops[-1].arrival == "04:40:00"
    assert stops[-1].journey_day == 2  # overnight train


def test_get_stops_unknown_train_is_empty(db):
    assert db.get_stops(UNKNOWN) == []


# --------------------------------------------------------------------------- #
# get_real_halt_stops
# --------------------------------------------------------------------------- #
def test_real_halt_stops_12658(db):
    halts = db.get_real_halt_stops(MAIL)
    codes = [s.station_code for s in halts]
    assert codes == ["SBC", "BNC", "BWT", "JTJ", "KPD", "AJJ", "PER", "MAS"]

    # termini have no halt_minutes (arrival/departure missing); the rest are > 0
    assert halts[0].halt_minutes is None and halts[-1].halt_minutes is None
    assert all(s.is_real_halt for s in halts[1:-1])
    assert halts[1].halt_minutes == 5.0  # BNC is the longest halt on this run


def test_real_halt_stops_is_subset_of_all_stops(db):
    all_stops = db.get_stops(MAIL)
    halts = db.get_real_halt_stops(MAIL)
    assert len(halts) < len(all_stops)  # most stops are pass-throughs
    all_orders = {s.stop_order for s in all_stops}
    assert all(s.stop_order in all_orders for s in halts)
    # never includes a pass-through
    assert not any(s.halt_minutes == 0 for s in halts)


def test_real_halt_stops_unknown_train_is_empty(db):
    assert db.get_real_halt_stops(UNKNOWN) == []


# --------------------------------------------------------------------------- #
# get_station / get_train
# --------------------------------------------------------------------------- #
def test_get_station(db):
    sbc = db.get_station("SBC")
    assert isinstance(sbc, Station)
    assert sbc.code == "SBC"
    assert "BANGALORE" in sbc.name.upper() or "BENGALURU" in sbc.name.upper()
    assert sbc.state == "Karnataka"
    assert sbc.has_coordinates
    assert 12.9 < sbc.lat < 13.1 and 77.5 < sbc.lon < 77.7


def test_get_station_unknown(db):
    assert db.get_station("ZZZZZZ") is None


def test_get_train(db):
    t = db.get_train(MAIL)
    assert isinstance(t, Train)
    assert t.number == MAIL
    assert "Mail" in t.name
    assert t.from_station_code == "SBC" and t.to_station_code == "MAS"
    assert t.distance_km and t.distance_km > 300
    assert isinstance(t.sleeper, bool)
    assert t.sleeper is True  # S1..S11 in the rake
    assert set(t.classes) >= {"SL", "3A", "2A"}
    assert t.duration_minutes == t.duration_h * 60 + t.duration_m


def test_get_train_unknown(db):
    assert db.get_train(UNKNOWN) is None


# --------------------------------------------------------------------------- #
# coach composition
# --------------------------------------------------------------------------- #
def test_get_coach_composition_12658(db):
    comp = db.get_coach_composition(MAIL)
    assert comp == [
        "EOG", "GS", "GS",
        "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9", "S10", "S11",
        "B1", "B2", "B3", "A1", "H1", "PC", "EOG",
    ]


def test_get_coach_composition_missing(db):
    assert db.get_coach_composition(NON_DEMO) == []
    assert db.get_coach_composition(UNKNOWN) == []


def test_coach_distance_12658(db):
    # S2 is at position 4, B1 at position 14 -> 10 positions apart
    assert db.coach_distance(MAIL, "S2", "B1") == 10
    assert db.coach_distance(MAIL, "B1", "S2") == 10  # symmetric
    assert db.coach_distance(MAIL, "S1", "S2") == 1  # adjacent
    assert db.coach_distance(MAIL, "S5", "S5") == 0


def test_coach_distance_matches_composition_list(db):
    comp = db.get_coach_composition(MAIL)
    assert db.coach_distance(MAIL, "A1", "H1") == abs(comp.index("A1") - comp.index("H1"))


def test_coach_distance_none_when_unavailable(db):
    assert db.coach_distance(NON_DEMO, "S1", "S2") is None  # no composition data
    assert db.coach_distance(MAIL, "S1", "Z9") is None  # coach not in rake


# --------------------------------------------------------------------------- #
# availability
# --------------------------------------------------------------------------- #
def test_get_availability_sbc_bnc(db):
    avail = db.get_availability(MAIL, "SBC", "BNC")
    assert avail, "SBC->BNC must have availability data"
    assert set(avail.values()) <= AVAILABILITY_STATUSES
    # only berth-bearing coaches appear; unreserved/service coaches are excluded
    assert "S1" in avail and "B1" in avail
    assert not {"EOG", "GS", "PC"} & avail.keys()
    # coaches come back in physical rake order
    comp = db.get_coach_composition(MAIL)
    positions = [comp.index(c) for c in avail]
    assert positions == sorted(positions)


def test_get_availability_full_journey(db):
    assert db.get_availability(MAIL, "SBC", "MAS")


def test_get_availability_empty_cases(db):
    assert db.get_availability(MAIL, "BNC", "SBC") == {}  # wrong direction
    assert db.get_availability(MAIL, "SBC", "BNCE") == {}  # pass-through, not bookable
    assert db.get_availability(NON_DEMO, "JAT", "UHP") == {}  # non-demo train
    assert db.get_availability(UNKNOWN, "SBC", "MAS") == {}


# --------------------------------------------------------------------------- #
# run calendar
# --------------------------------------------------------------------------- #
def test_runs_on_date_12217_not_wednesday(db):
    assert dt.date(2026, 9, 16).strftime("%a").upper() == "WED"
    assert db.runs_on_date(KERALA_SK, "2026-09-16") is False


def test_runs_on_date_12217_tue_fri(db):
    assert db.runs_on_date(KERALA_SK, "2026-09-15") is True  # Tuesday
    assert db.runs_on_date(KERALA_SK, "2026-09-18") is True  # Friday


def test_runs_on_date_12658_daily(db):
    assert db.runs_on_date(MAIL, "2026-09-16") is True
    for day in range(10, 26):
        assert db.runs_on_date(MAIL, f"2026-09-{day:02d}") is True


def test_runs_on_date_outside_window_or_unknown(db):
    assert db.runs_on_date(MAIL, "2026-09-09") is False
    assert db.runs_on_date(MAIL, "2026-09-26") is False
    assert db.runs_on_date(NON_DEMO, "2026-09-16") is False
    assert db.runs_on_date(UNKNOWN, "2026-09-16") is False


# --------------------------------------------------------------------------- #
# trains through station
# --------------------------------------------------------------------------- #
def test_get_all_trains_through_station(db):
    trains = db.get_all_trains_through_station("JTJ")  # Jolarpettai Jn, busy junction
    assert MAIL in trains
    assert trains == sorted(trains)
    assert len(trains) == len(set(trains))
    assert len(trains) > 50


def test_get_all_trains_through_station_unknown(db):
    assert db.get_all_trains_through_station("ZZZZZZ") == []


# --------------------------------------------------------------------------- #
# injection safety
# --------------------------------------------------------------------------- #
def test_parameterized_queries_are_injection_safe(db):
    evil = "' OR 1=1 --"
    assert db.get_stops(evil) == []
    assert db.get_station(evil) is None
    assert db.get_train(evil) is None
    assert db.get_all_trains_through_station(evil) == []
    assert db.runs_on_date(evil, evil) is False
    assert db.get_availability(evil, evil, evil) == {}


# --------------------------------------------------------------------------- #
# the DB is not modified by using the store
# --------------------------------------------------------------------------- #
def _sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def test_store_usage_does_not_modify_db():
    before = _sha256(DEFAULT_DB_PATH)
    with RailDataStore() as store:
        store.get_stops(MAIL)
        store.get_real_halt_stops(MAIL)
        store.get_availability(MAIL, "SBC", "MAS")
        store.get_all_trains_through_station("MAS")
    assert _sha256(DEFAULT_DB_PATH) == before


def test_get_availability_from_matches_per_pair_queries(db):
    by_dest = db.get_availability_from(MAIL, "SBC")
    assert set(by_dest) == {"BNC", "BWT", "JTJ", "KPD", "AJJ", "PER", "MAS"}
    for to, coaches in by_dest.items():
        assert coaches == db.get_availability(MAIL, "SBC", to)
    assert db.get_availability_from(MAIL, "BNCE") == {}   # pass-through: not bookable
    assert db.get_availability_from(NON_DEMO, "JAT") == {}


def test_get_trains_running_through_matches_filtered_query(db):
    fast = db.get_trains_running_through("SBC", "2026-09-16")
    slow = [t for t in db.get_all_trains_through_station("SBC") if db.runs_on_date(t, "2026-09-16")]
    assert fast == slow == ["12609", "12658"]
    assert db.get_trains_running_through("SBC", "2026-09-26") == []
    assert db.get_trains_running_through("ZZZZZ", "2026-09-16") == []
