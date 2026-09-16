"""Boundary and real-result contract checks for the browser adapter."""
import json

import pytest

from cli import build_system
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
def test_direct_trains_returns_real_runs(web_system):
    """The whole network is covered, not just the six demo trains."""
    result = direct_trains(web_system, {"origin": "HWH", "destination": "NDLS"})
    numbers = {t["train_number"] for t in result["trains"]}
    assert "12301" in numbers  # Howrah - New Delhi Rajdhani
    assert all(t["duration_minutes"] > 0 for t in result["trains"])


def test_direct_trains_respects_direction(web_system):
    forward = {t["train_number"] for t in
               direct_trains(web_system, {"origin": "SBC", "destination": "MAS"})["trains"]}
    backward = {t["train_number"] for t in
                direct_trains(web_system, {"origin": "MAS", "destination": "SBC"})["trains"]}
    assert forward and backward and not forward & backward


def test_direct_trains_rejects_identical_stations(web_system):
    with pytest.raises(ValueError):
        direct_trains(web_system, {"origin": "SBC", "destination": "SBC"})


def test_direct_trains_rejects_unknown_station(web_system):
    with pytest.raises(ValueError):
        direct_trains(web_system, {"origin": "SBC", "destination": "NOPE"})
