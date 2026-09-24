"""RailRadar provider. Offline: responses are built to the documented schema.

The one networked test is skipped unless RAILRADAR_API_KEY is set, so the
suite neither needs a key nor spends the 1,000-request monthly free quota.
"""
import datetime as dt
import json
import os

import pytest

from src import railradar
from src.live_trains import LiveLookupError

BETWEEN = {
    "success": True,
    "data": {
        "from": {"code": "SBC", "name": "Ksr Bengaluru"},
        "to": {"code": "MAS", "name": "Mgr Chennai Ctr"},
        "count": 2,
        "trains": [
            {"train": {"number": "12008", "name": "Shatabdi Express", "type": "Shatabdi",
                       "category": "Premium", "runDays": ["Mon", "Tue", "Wed", "Fri", "Sat", "Sun"]},
             "from": {"departure": "16:10", "day": 1, "sequence": 1},
             "to": {"arrival": "21:25", "day": 1, "sequence": 9},
             "distance": 362, "duration": 315, "totalHaltsBetween": 2,
             "live": {"type": "RUNNING", "delayMinutes": 12}},
            {"train": {"number": "12640", "name": "Brindavan Express", "type": "Express",
                       "category": "Superfast", "runDays": ["Mon", "Tue", "Wed", "Thu",
                                                            "Fri", "Sat", "Sun"]},
             "from": {"departure": "15:15", "day": 1, "sequence": 1},
             "to": {"arrival": "21:15", "day": 1, "sequence": 12},
             "distance": 362, "duration": 360, "totalHaltsBetween": 11, "live": {}},
        ],
    },
    "meta": {"traceId": "x", "timestamp": "2026-09-16T10:00:00+05:30"},
}

@pytest.fixture
def keyed(monkeypatch):
    monkeypatch.setenv(railradar.API_KEY_ENV, "rr_live_test")


def _stub(monkeypatch, payload):
    monkeypatch.setattr(railradar, "_request", lambda path, params: payload["data"])


def test_unconfigured_without_key(monkeypatch):
    monkeypatch.delenv(railradar.API_KEY_ENV, raising=False)
    assert not railradar.is_configured()
    with pytest.raises(LiveLookupError, match=railradar.API_KEY_ENV):
        railradar.fetch_between_stations("SBC", "MAS")


def test_configured_with_key(keyed):
    assert railradar.is_configured()


def test_between_stations_maps_fields(keyed, monkeypatch):
    _stub(monkeypatch, BETWEEN)
    trains = railradar.fetch_between_stations("SBC", "MAS")
    assert len(trains) == 2
    shatabdi = trains[0]
    assert shatabdi.number == "12008"
    assert shatabdi.departure == "16:10" and shatabdi.arrival == "21:25"
    assert shatabdi.duration_minutes == 315       # from duration, not a time subtraction
    assert shatabdi.halts == 2
    assert shatabdi.provider == "railradar.in"


def test_run_days_become_a_monday_first_mask(keyed, monkeypatch):
    _stub(monkeypatch, BETWEEN)
    shatabdi, brindavan = railradar.fetch_between_stations("SBC", "MAS")
    assert shatabdi.running_days == "1110111"     # Thursday off, index 3
    assert not shatabdi.runs_on(dt.date(2026, 9, 17))   # a Thursday
    assert brindavan.runs_daily


def test_entries_without_required_fields_are_skipped(keyed, monkeypatch):
    monkeypatch.setattr(railradar, "_request",
                        lambda path, params: {"trains": [{"train": {"name": "No number"}},
                                                         BETWEEN["data"]["trains"][0]]})
    assert [t.number for t in railradar.fetch_between_stations("SBC", "MAS")] == ["12008"]


def test_no_trains_is_an_answer_not_an_error(keyed, monkeypatch):
    """An empty result means no direct service, so callers must not fall back
    to the 2020 snapshot and show trains that may no longer run."""
    monkeypatch.setattr(railradar, "_request", lambda path, params: {"trains": []})
    assert railradar.fetch_between_stations("SBC", "MAS") == []


def test_api_error_becomes_lookup_error(keyed, monkeypatch):
    """A success:false envelope must not be read as data."""
    class FakeResponse:
        def read(self): return json.dumps(
            {"success": False, "error": {"code": "RATE_LIMITED", "message": "quota exceeded"}}).encode()
        def __enter__(self): return self
        def __exit__(self, *a): pass

    monkeypatch.setattr(railradar.urllib.request, "urlopen", lambda *a, **k: FakeResponse())
    with pytest.raises(LiveLookupError, match="quota exceeded"):
        railradar.fetch_between_stations("SBC", "MAS")


def test_key_is_sent_as_bearer_and_not_in_the_url(keyed, monkeypatch):
    seen = {}

    class FakeResponse:
        def read(self): return json.dumps({"success": True, "data": {"trains": []}}).encode()
        def __enter__(self): return self
        def __exit__(self, *a): pass

    def capture(request, **kwargs):
        seen["url"] = request.full_url
        seen["auth"] = request.get_header("Authorization")
        return FakeResponse()

    monkeypatch.setattr(railradar.urllib.request, "urlopen", capture)
    railradar.fetch_between_stations("SBC", "MAS")
    assert seen["auth"] == "Bearer rr_live_test"
    assert "rr_live_test" not in seen["url"]


@pytest.mark.skipif(not os.environ.get("RAILRADAR_API_KEY"),
                    reason="set RAILRADAR_API_KEY to call the real API")
def test_live_call_against_real_api():
    trains = railradar.fetch_between_stations("SBC", "MAS")
    assert trains and all(t.provider == "railradar.in" for t in trains)
