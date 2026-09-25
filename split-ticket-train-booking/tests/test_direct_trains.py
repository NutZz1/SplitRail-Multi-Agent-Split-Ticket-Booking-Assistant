"""Direct origin -> destination lookup (RailDataStore.get_direct_trains)."""
import pytest

from src.data_store import RailDataStore
from tests.markers import requires_full_network


@pytest.fixture(scope="module")
def store():
    with RailDataStore() as s:
        yield s


def _numbers(runs):
    return {r.train_number for r in runs}


@requires_full_network
def test_finds_known_direct_trains(store):
    """Brindavan (12640) and Lalbagh (12608) both run SBC -> MAS."""
    assert {"12640", "12608"} <= _numbers(store.get_direct_trains("SBC", "MAS"))


@requires_full_network
def test_direction_is_respected(store):
    """12610 runs SBC->MAS and 12609 the reverse; neither appears on both."""
    forward = _numbers(store.get_direct_trains("SBC", "MAS"))
    backward = _numbers(store.get_direct_trains("MAS", "SBC"))
    assert "12610" in forward and "12610" not in backward
    assert "12609" in backward and "12609" not in forward


def test_unknown_station_returns_empty(store):
    assert store.get_direct_trains("SBC", "ZZZZ") == []


def test_same_station_returns_empty(store):
    """A stop can never precede itself, so a degenerate pair yields nothing."""
    assert store.get_direct_trains("SBC", "SBC") == []


def test_results_sorted_by_departure(store):
    departures = [r.from_departure for r in store.get_direct_trains("SBC", "MAS")]
    assert departures == sorted(departures)


@requires_full_network
def test_duration_handles_overnight(store):
    """Mumbai Rajdhani departs NDLS 16:30 and arrives BCT 08:35 next day."""
    rajdhani = next(r for r in store.get_direct_trains("NDLS", "BCT")
                    if r.train_number == "12952")
    assert rajdhani.duration_minutes == 16 * 60 + 5
    assert rajdhani.to_day == rajdhani.from_day + 1


@requires_full_network
def test_stops_between_counts_halts_not_passthroughs(store):
    """Shatabdi 12028 makes only a couple of commercial halts SBC -> MAS."""
    shatabdi = next(r for r in store.get_direct_trains("SBC", "MAS")
                    if r.train_number == "12028")
    assert shatabdi.stops_between < 5


def test_passthrough_station_is_not_boardable(store):
    """A station the train races through must not qualify as an endpoint."""
    for run in store.get_direct_trains("SBC", "MAS"):
        stops = {s.station_code: s for s in store.get_stops(run.train_number)}
        for code in ("SBC", "MAS"):
            halt = stops[code].halt_minutes
            assert halt is None or halt > 0


def test_date_filter_excludes_non_running_trains(store):
    """An implausible date leaves nothing, while the unfiltered query does not."""
    assert store.get_direct_trains("SBC", "MAS") != []
    assert store.get_direct_trains("SBC", "MAS", date_str="1999-01-01") == []
