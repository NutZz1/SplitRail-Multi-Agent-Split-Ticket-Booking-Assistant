"""Boundary and real-result contract checks for the browser adapter."""
import json

import pytest

from cli import build_system
import web
from web import direct_trains, search


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


def test_hard_class_requirement_returns_no_results(web_system):
    result = search(web_system, {**BASE, "travel_class": "CC"})
    assert result["options"] == []
    assert result["failure_reason"]


@pytest.mark.parametrize("change", [
    {"origin": "INVALID"}, {"destination": "SBC"}, {"date": "2027-01-01"},
    {"travel_class": "INVALID"}, {"max_transfers": -1}, {"max_transfers": 3},
    {"max_transfers": True}, {"max_transfers": "2"},
])
def test_invalid_queries_are_rejected(web_system, change):
    with pytest.raises(ValueError):
        search(web_system, {**BASE, **change})


# --- direct-train endpoint -------------------------------------------------
@pytest.fixture
def offline(monkeypatch):
    """Force the bundled timetable so these tests never touch erail.in."""
    monkeypatch.setattr(web, "LIVE_LOOKUPS", False)


# --- direct-train endpoint -------------------------------------------------
def test_direct_trains_returns_real_runs(web_system, offline):
    """The whole network is covered, not just the six demo trains."""
    result = direct_trains(web_system, {"origin": "HWH", "destination": "NDLS"})
    numbers = {t["train_number"] for t in result["trains"]}
    assert result["source"] == "local"
    assert "12301" in numbers  # Howrah - New Delhi Rajdhani
    assert all(t["duration_minutes"] > 0 for t in result["trains"])


def test_direct_trains_respects_direction(web_system, offline):
    forward = {t["train_number"] for t in
               direct_trains(web_system, {"origin": "SBC", "destination": "MAS"})["trains"]}
    backward = {t["train_number"] for t in
                direct_trains(web_system, {"origin": "MAS", "destination": "SBC"})["trains"]}
    assert forward and backward and not forward & backward


def test_direct_trains_rejects_identical_stations(web_system, offline):
    with pytest.raises(ValueError):
        direct_trains(web_system, {"origin": "SBC", "destination": "SBC"})


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
    result = direct_trains(web_system, {"origin": "SBC", "destination": "MAS",
                                        "date": "2026-09-16"})
    assert result["source"] == "live"
    assert result["trains"][0]["train_number"] == "12345"
    assert result["trains"][0]["running_days"] == ["Mon", "Tue", "Wed", "Thu",
                                                   "Fri", "Sat", "Sun"]


def test_falls_back_to_local_when_live_fails(web_system, monkeypatch):
    """An unreachable live service must not break the page."""
    from src.live_trains import LiveLookupError

    def boom(*a, **k):
        raise LiveLookupError("erail is down")

    monkeypatch.setattr(web, "LIVE_LOOKUPS", True)
    monkeypatch.setattr(web, "fetch_between_stations", boom)
    result = direct_trains(web_system, {"origin": "SBC", "destination": "MAS"})
    assert result["source"] == "local"
    assert "erail is down" in result["notice"]
    assert result["trains"]


def test_live_results_are_filtered_by_travel_date(web_system, monkeypatch):
    """A Sunday-only train is hidden on a Wednesday and counted separately."""
    from src.live_trains import LiveTrain
    sunday_only = LiveTrain(number="22697", name="SUNDAY EXP", from_code="SBC",
                            from_name="Bengaluru", to_code="MAS", to_name="Chennai",
                            departure="04:35", arrival="11:10", travel_time="06:35",
                            running_days="0000001", train_origin_code="SBC",
                            train_dest_code="MAS")
    monkeypatch.setattr(web, "LIVE_LOOKUPS", True)
    monkeypatch.setattr(web, "fetch_between_stations", lambda *a, **k: [sunday_only])
    wednesday = direct_trains(web_system, {"origin": "SBC", "destination": "MAS",
                                           "date": "2026-09-16"})
    assert wednesday["trains"] == [] and wednesday["other_days"] == 1
    sunday = direct_trains(web_system, {"origin": "SBC", "destination": "MAS",
                                        "date": "2026-09-20"})
    assert len(sunday["trains"]) == 1 and sunday["other_days"] == 0


def test_bad_date_is_rejected(web_system):
    with pytest.raises(ValueError):
        direct_trains(web_system, {"origin": "SBC", "destination": "MAS",
                                   "date": "16-09-2026"})
