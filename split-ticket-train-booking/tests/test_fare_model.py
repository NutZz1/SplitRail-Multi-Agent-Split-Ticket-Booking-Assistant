"""Tests for src.distance and src.fare_model against real coordinates."""

from __future__ import annotations

import datetime as dt

import pytest

from src.distance import haversine_km, route_distance_km, segment_distance_km
from src.fare_model import FARE_PER_KM, compute_fare, compute_total_fare, ticket_distance_km
from src.state import Ticket

MAIL = "12658"


# --------------------------------------------------------------------------- #
# distance
# --------------------------------------------------------------------------- #
def test_haversine_known_value():
    # Bangalore City (12.978, 77.570) to Chennai Central (13.083, 80.276): ~293 km great-circle
    assert haversine_km(12.978, 77.570, 13.083, 80.276) == pytest.approx(293.5, abs=2)
    assert haversine_km(10, 20, 10, 20) == 0.0


def test_segment_distance_sbc_mas_straight_line(store):
    km = segment_distance_km("SBC", "MAS", store)
    print(f"\nSBC-MAS straight-line: {km:.1f} km")
    assert km == pytest.approx(293.5, abs=3)
    assert segment_distance_km("MAS", "SBC", store) == pytest.approx(km)


def test_route_distance_sbc_mas_follows_the_track(store):
    km = route_distance_km(MAIL, "SBC", "MAS", store)
    real = store.get_train(MAIL).distance_km
    print(f"\n12658 SBC->MAS: haversine sum along all stops {km:.1f} km, real track {real} km")
    assert km == pytest.approx(347.4, abs=3)    # the figure established in earlier validation
    assert 293 < km <= real                      # longer than the crow flies, never longer than the track
    assert km / real > 0.9


def test_route_distance_is_additive_over_halts(store):
    a = route_distance_km(MAIL, "SBC", "KPD", store)
    b = route_distance_km(MAIL, "KPD", "MAS", store)
    whole = route_distance_km(MAIL, "SBC", "MAS", store)
    assert a + b == pytest.approx(whole, abs=1e-6)


def test_route_distance_invalid_inputs(store):
    assert route_distance_km(MAIL, "MAS", "SBC", store) is None   # wrong direction
    assert route_distance_km(MAIL, "SBC", "ZZZZZ", store) is None
    assert route_distance_km("99999", "SBC", "MAS", store) is None


def test_segment_distance_missing_coordinates_returns_none(store):
    # 293 stations have no lat/lon; AAV is one of them and appears in real schedules
    row = store._conn.execute("SELECT code FROM stations WHERE lat IS NULL LIMIT 1").fetchone()
    code = row["code"]
    assert not store.get_station(code).has_coordinates
    assert segment_distance_km(code, "MAS", store) is None
    assert segment_distance_km("MAS", code, store) is None
    assert segment_distance_km("NOT_A_STATION", "MAS", store) is None


def test_route_distance_bridges_over_coordinateless_intermediate_stops(store):
    """A train whose route passes a coordinate-less stop still gets a distance (the stop is skipped)."""
    row = store._conn.execute(
        """
        SELECT ss.train_number, ss.station_code FROM schedule_stops ss
        JOIN stations s ON s.code = ss.station_code
        WHERE s.lat IS NULL AND ss.stop_order > 0
        LIMIT 1
        """
    ).fetchone()
    train, bad = row["train_number"], row["station_code"]
    stops = [s.station_code for s in store.get_stops(train)]
    i = stops.index(bad)
    before, after = stops[i - 1], stops[i + 1] if i + 1 < len(stops) else None
    if after is None or not (store.get_station(before) and store.get_station(before).has_coordinates
                             and store.get_station(after) and store.get_station(after).has_coordinates):
        pytest.skip("neighbours of the coordinate-less stop also lack coordinates")
    km = route_distance_km(train, before, after, store)
    print(f"\ntrain {train}: {before} -> [{bad}: no coords] -> {after} = {km:.1f} km (bridged)")
    assert km is not None and km > 0
    assert route_distance_km(train, bad, after, store) is None  # endpoint without coords


# --------------------------------------------------------------------------- #
# fares
# --------------------------------------------------------------------------- #
def _ticket(train, a, b, coach, cls):
    return Ticket(train, a, b, coach, cls, dt.datetime(2026, 9, 16, 22, 45), dt.datetime(2026, 9, 17, 4, 40), "CONFIRMED")


def test_fare_constants_are_marked_synthetic():
    import inspect
    import src.fare_model as fm
    src_text = inspect.getsource(fm)
    assert "SYNTHETIC" in src_text
    assert set(FARE_PER_KM) >= {"SL", "3A", "2A", "1A", "CC", "2S"}
    assert FARE_PER_KM["SL"] < FARE_PER_KM["3A"] < FARE_PER_KM["2A"] < FARE_PER_KM["1A"]


def test_compute_fare_sbc_mas_by_class(store):
    km = ticket_distance_km(_ticket(MAIL, "SBC", "MAS", "S1", "SL"), store)
    print(f"\nSBC->MAS on 12658 = {km:.1f} km")
    for coach, cls in [("S1", "SL"), ("B1", "3A"), ("A1", "2A"), ("H1", "1A")]:
        fare = compute_fare(_ticket(MAIL, "SBC", "MAS", coach, cls), store)
        print(f"  {cls}: Rs {fare}")
        assert fare == pytest.approx(km * FARE_PER_KM[cls], abs=0.01)
    assert compute_fare(_ticket(MAIL, "SBC", "MAS", "S1", "SL"), store) < compute_fare(_ticket(MAIL, "SBC", "MAS", "H1", "1A"), store)


def test_compute_fare_none_for_unknown_class_or_missing_coords(store):
    assert compute_fare(_ticket(MAIL, "SBC", "MAS", "GS", None), store) is None
    assert compute_fare(_ticket(MAIL, "SBC", "MAS", "X1", "XX"), store) is None
    code = store._conn.execute("SELECT code FROM stations WHERE lat IS NULL LIMIT 1").fetchone()["code"]
    assert compute_fare(_ticket("99999", code, "MAS", "S1", "SL"), store) is None


def test_compute_fare_falls_back_to_straight_line_off_route(store):
    # stations not on the ticket's train route: route distance fails, straight-line used
    t = _ticket("99999", "SBC", "MAS", "S1", "SL")
    assert compute_fare(t, store) == pytest.approx(segment_distance_km("SBC", "MAS", store) * FARE_PER_KM["SL"], abs=0.01)


def test_compute_total_fare_sums_and_never_partial(store):
    legs = (_ticket(MAIL, "SBC", "BNC", "S1", "SL"), _ticket(MAIL, "BNC", "MAS", "B1", "3A"))
    total = compute_total_fare(legs, store)
    assert total == pytest.approx(sum(compute_fare(t, store) for t in legs), abs=0.01)
    bad = legs + (_ticket(MAIL, "SBC", "MAS", "GS", None),)
    assert compute_total_fare(bad, store) is None   # one unknown leg -> no total at all
    assert compute_total_fare((), store) is None
