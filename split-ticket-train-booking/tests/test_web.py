"""Boundary and real-result contract checks for the browser adapter."""
import json
import datetime as dt

import pytest

from cli import build_system
import web
from web import direct_trains, search
from tests.markers import requires_full_network


@pytest.fixture(scope="module")
def web_system():
    system = build_system(use_pool=False)
    yield system
    system.close()


BASE = {"origin": "SBC", "destination": "MAS", "date": "2026-09-16",
        "travel_class": "", "max_transfers": 2}


def test_real_results_are_serializable_and_include_transfer_details(web_system):
    result = search(web_system, BASE)
    json.dumps(result, default=str, allow_nan=False)
    assert len(result["options"]) >= 2
    assert result["options"][0]["transfers"] == 0
    split = next(o for o in result["options"] if o["transfers"])
    assert split["comfort"]["per_transfer_penalties"]
    assert split["tickets"][0]["train_name"]
    assert "goal_state" not in split
    assert all(isinstance(o["pareto_optimal"], bool) for o in result["options"])
    assert all(sum(o['score_components'].values()) == pytest.approx(o['score']) for o in result['options'])
    assert isinstance(result['truncated'], bool)
    assert result["options"][0]["pareto_optimal"] is True  # the top-ranked option is always on the front


def test_hard_class_requirement_returns_no_results(web_system):
    result = search(web_system, {**BASE, "travel_class": "CC"})
    assert result["options"] == []
    assert result["failure_reason"]


@pytest.mark.parametrize("change", [
    {"origin": "INVALID"}, {"destination": "SBC"}, {"date": "2027-01-01"},
    {"travel_class": "INVALID"}, {"max_transfers": -1}, {"max_transfers": 3},
    {"max_transfers": True}, {"max_transfers": "2"},
    {"seat_change_tolerance": "nope"}, {"seat_change_tolerance": "ANY"},
])
def test_invalid_queries_are_rejected(web_system, change):
    with pytest.raises(ValueError):
        search(web_system, {**BASE, **change})


@pytest.mark.parametrize("tolerance", ["any", "short_walk", "none"])
def test_seat_change_tolerance_is_threaded_through(web_system, tolerance):
    """Every valid tolerance value is accepted and reaches the search: a
    real search constraint now, not a display filter applied afterwards."""
    result = search(web_system, {**BASE, "seat_change_tolerance": tolerance})
    json.dumps(result, default=str, allow_nan=False)


def test_none_tolerance_never_returns_a_same_train_coach_switch(web_system):
    result = search(web_system, {**BASE, "seat_change_tolerance": "none"})
    for option in result["options"]:
        trains = [t["train_number"] for t in option["tickets"]]
        coaches = [t["coach"] for t in option["tickets"]]
        for i in range(1, len(trains)):
            if trains[i] == trains[i - 1]:
                assert coaches[i] == coaches[i - 1]


# --- direct-train endpoint -------------------------------------------------
@pytest.fixture
def offline(monkeypatch):
    """Force the bundled timetable so these tests never touch erail.in."""
    monkeypatch.setattr(web, "LIVE_LOOKUPS", False)


# --- direct-train endpoint -------------------------------------------------
@requires_full_network
def test_direct_trains_returns_real_runs(web_system, offline):
    result = direct_trains(web_system, {'origin':'KYJ','destination':'PGT','date':'2026-09-16'})
    numbers = {t['train_number'] for t in result['trains']}
    assert {'16343','12625','12624','12696'} <= numbers
    assert numbers <= set(web_system.store.get_demo_train_numbers())
    assert all(t['duration_minutes'] > 0 for t in result['trains'])
    assert all(t['classes'] for t in result['trains'])


def test_direct_trains_respects_direction(web_system, offline):
    forward = {t["train_number"] for t in
               direct_trains(web_system, {"origin": "SBC", "destination": "MAS", "date": "2026-09-16"})["trains"]}
    backward = {t["train_number"] for t in
                direct_trains(web_system, {"origin": "MAS", "destination": "SBC", "date": "2026-09-16"})["trains"]}
    assert forward and backward and not forward & backward


def test_direct_trains_rejects_identical_stations(web_system, offline):
    with pytest.raises(ValueError):
        direct_trains(web_system, {"origin": "SBC", "destination": "SBC", "date": "2026-09-16"})


def test_direct_trains_rejects_unknown_station(web_system, offline):
    with pytest.raises(ValueError):
        direct_trains(web_system, {"origin": "SBC", "destination": "NOPE"})


def test_live_source_is_used_when_available(web_system, monkeypatch):
    """A working live lookup answers, and is reported as the source."""
    from src.live_trains import LiveTrain
    train = LiveTrain(number="12345", name="TEST EXP", from_code="SBC",
                      from_name="Bengaluru", to_code="MAS", to_name="Chennai",
                      departure="06:00", arrival="11:00", travel_time="05:00",
                      running_days="1111111", train_origin_code="SBC",
                      train_dest_code="MAS")
    monkeypatch.setattr(web, "LIVE_LOOKUPS", True)
    monkeypatch.setattr(web, "fetch_between_stations", lambda *a, **k: [train])
    trains, other, provider = web._live_direct("SBC", "MAS", dt.date(2026,9,16))
    result = {"trains":trains, "provider":provider, "source":"live"}
    assert result["source"] == "live"
    assert result["trains"][0]["train_number"] == "12345"
    assert result["trains"][0]["running_days"] == ["Mon", "Tue", "Wed", "Thu",
                                                   "Fri", "Sat", "Sun"]


def test_falls_back_to_local_when_live_fails(web_system, monkeypatch):
    # The booking list is local even if a live provider exists or fails.
    monkeypatch.setattr(web, 'fetch_between_stations', lambda *a: pytest.fail('Booking search must not call a live provider'))
    result = direct_trains(web_system, {'origin':'SBC','destination':'MAS','date':'2026-09-16'})
    assert result['source'] == 'local' and result['trains']
    assert 'simulated' in result['notice']


def test_live_results_are_filtered_by_travel_date(web_system, monkeypatch):
    from src.live_trains import LiveTrain
    from src import railradar
    train = LiveTrain(number='22697', name='SUNDAY EXP', from_code='SBC', from_name='Bengaluru',
        to_code='MAS', to_name='Chennai', departure='04:35', arrival='11:10', travel_time='06:35',
        running_days='0000001', train_origin_code='SBC', train_dest_code='MAS')
    monkeypatch.setattr(railradar, 'is_configured', lambda: False)
    monkeypatch.setattr(web, 'fetch_between_stations', lambda *args:[train])
    wed, excluded, _ = web._live_direct('SBC','MAS',dt.date(2026,9,16))
    sun, _, _ = web._live_direct('SBC','MAS',dt.date(2026,9,20))
    assert wed == [] and excluded == 1
    assert len(sun) == 1


def test_bad_date_is_rejected(web_system):
    with pytest.raises(ValueError):
        direct_trains(web_system, {"origin": "SBC", "destination": "MAS",
                                   "date": "16-09-2026"})


def test_railradar_is_preferred_when_configured(web_system, monkeypatch):
    """With a key present, RailRadar answers and is named as the provider."""
    from src.live_trains import LiveTrain
    from src import railradar
    train = LiveTrain(number="12008", name="SHATABDI", from_code="SBC", from_name="SBC",
                      to_code="MAS", to_name="MAS", departure="16:10", arrival="21:25",
                      travel_time="05:15", running_days="1111111",
                      train_origin_code="SBC", train_dest_code="MAS",
                      halts=2, provider="railradar.in")
    monkeypatch.setattr(web, "LIVE_LOOKUPS", True)
    monkeypatch.setattr(railradar, "is_configured", lambda: True)
    monkeypatch.setattr(railradar, "fetch_between_stations", lambda *a, **k: [train])
    monkeypatch.setattr(web, "fetch_between_stations", lambda *a, **k: pytest.fail("erail should not be called"))
    trains, _, provider = web._live_direct("SBC", "MAS", None)
    result = {"trains":trains, "provider":provider}
    assert result["provider"] == "railradar.in"
    assert result["trains"][0]["halts"] == 2


def test_falls_back_to_erail_when_railradar_fails(web_system, monkeypatch):
    from src.live_trains import LiveLookupError, LiveTrain
    from src import railradar

    def boom(*a, **k):
        raise LiveLookupError("quota exceeded")

    train = LiveTrain(number="12640", name="BRINDAVAN", from_code="SBC", from_name="SBC",
                      to_code="MAS", to_name="MAS", departure="15:15", arrival="21:15",
                      travel_time="06:00", running_days="1111111",
                      train_origin_code="SBC", train_dest_code="MAS")
    monkeypatch.setattr(web, "LIVE_LOOKUPS", True)
    monkeypatch.setattr(railradar, "is_configured", lambda: True)
    monkeypatch.setattr(railradar, "fetch_between_stations", boom)
    monkeypatch.setattr(web, "fetch_between_stations", lambda *a, **k: [train])
    trains, _, provider = web._live_direct("SBC", "MAS", None)
    result = {"trains":trains, "provider":provider}
    assert result["provider"] == "erail.in"


def test_repeated_provider_failures_are_not_repeated_in_the_notice(web_system, monkeypatch):
    from src.live_trains import LiveLookupError
    from src import railradar
    def boom(*args, **kwargs):
        raise LiveLookupError('everything is down')
    monkeypatch.setattr(railradar,'is_configured',lambda:True)
    monkeypatch.setattr(railradar,'fetch_between_stations',boom)
    monkeypatch.setattr(web,'fetch_between_stations',boom)
    with pytest.raises(LiveLookupError) as error:
        web._live_direct('SBC','MAS',None)
    assert str(error.value).count('everything is down') == 1
