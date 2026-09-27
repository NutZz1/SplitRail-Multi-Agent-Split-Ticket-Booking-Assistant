"""
Tests for src.rail_graph against the real railway.db.

Covers the time arithmetic in isolation (synthetic Stop objects) and the
built graph's structure using train 12658's known real-halt route.
"""

from __future__ import annotations

import networkx as nx

from src.data_store import Stop
from src.rail_graph import GraphBuildStats, build_graph, segment_minutes, time_to_minutes
from tests.markers import requires_full_network

MAIL = "12658"
MAIL_HALTS = ["SBC", "BNC", "BWT", "JTJ", "KPD", "AJJ", "PER", "MAS"]


def _stop(code: str, arrival, departure, day, order: int = 0) -> Stop:
    return Stop(
        train_number="T", stop_order=order, station_code=code, station_name=code,
        arrival=arrival, departure=departure, journey_day=day, halt_minutes=2.0,
    )


# --------------------------------------------------------------------------- #
# time arithmetic
# --------------------------------------------------------------------------- #
def test_time_to_minutes():
    assert time_to_minutes("00:00:00") == 0
    assert time_to_minutes("22:45:00") == 22 * 60 + 45
    assert time_to_minutes("04:40") == 4 * 60 + 40


def test_segment_same_day():
    a = _stop("A", "10:00:00", "10:05:00", 1)
    b = _stop("B", "11:35:00", "11:37:00", 1)
    assert segment_minutes(a, b) == 90


def test_segment_overnight_with_journey_day():
    a = _stop("A", None, "22:45:00", 1)
    b = _stop("B", "04:40:00", None, 2)
    assert segment_minutes(a, b) == (24 * 60 + 4 * 60 + 40) - (22 * 60 + 45)  # 355


def test_segment_multi_day_gap():
    a = _stop("A", None, "12:00:00", 1)
    b = _stop("B", "12:00:00", None, 3)
    assert segment_minutes(a, b) == 2 * 1440


def test_segment_fallback_without_journey_day_rolls_over_midnight():
    a = _stop("A", None, "23:50:00", None)
    b = _stop("B", "00:10:00", None, None)
    assert segment_minutes(a, b) == 20
    # one side missing day also triggers the fallback
    b2 = _stop("B", "00:10:00", None, 2)
    assert segment_minutes(a, b2) == 20


def test_segment_fallback_without_journey_day_same_day():
    a = _stop("A", None, "08:00:00", None)
    b = _stop("B", "09:30:00", None, None)
    assert segment_minutes(a, b) == 90


def test_segment_missing_timestamps_is_none():
    assert segment_minutes(_stop("A", None, None, 1), _stop("B", "09:00:00", None, 1)) is None
    assert segment_minutes(_stop("A", None, "08:00:00", 1), _stop("B", None, None, 1)) is None


def test_segment_inconsistent_source_data_is_negative():
    # journey_day says same day but the clock went backwards: caller must skip
    a = _stop("A", None, "23:05:00", 1)
    b = _stop("B", "07:15:00", None, 1)
    assert segment_minutes(a, b) < 0


# --------------------------------------------------------------------------- #
# graph structure
# --------------------------------------------------------------------------- #
def test_graph_is_directed(graph):
    assert isinstance(graph, nx.DiGraph)


@requires_full_network
def test_graph_size_is_sane(graph):
    n, e = graph.number_of_nodes(), graph.number_of_edges()
    print(f"\nGraph size: {n:,} nodes, {e:,} edges")
    assert n > 1000
    assert e > 1000
    assert n < 8990  # not every station is a real halt for some train


@requires_full_network
def test_build_stats_attached(graph):
    stats = graph.graph["build_stats"]
    assert isinstance(stats, GraphBuildStats)
    print(f"\n{stats.summary(graph)}")
    assert stats.trains_contributing > 1000
    assert stats.trains_contributing <= stats.trains_seen
    assert stats.edges_added == graph.number_of_edges()


def test_all_edge_weights_positive(graph):
    bad = [(u, v, d) for u, v, d in graph.edges(data=True) if not d["weight"] > 0]
    assert bad == []


def test_all_edges_carry_debug_attributes(graph):
    for _, _, d in graph.edges(data=True):
        assert isinstance(d["train_number"], str)
        assert d["train_count"] >= 1


def test_12658_real_halts_match_known_list(store):
    assert [s.station_code for s in store.get_real_halt_stops(MAIL)] == MAIL_HALTS


def test_12658_consecutive_edges_exist_with_positive_weight(graph, store):
    stops = store.get_real_halt_stops(MAIL)
    for a, b in zip(stops, stops[1:]):
        assert graph.has_edge(a.station_code, b.station_code), f"missing {a.station_code}->{b.station_code}"
        w = graph[a.station_code][b.station_code]["weight"]
        assert w > 0
        # graph keeps the MINIMUM over all trains, so it can't exceed 12658's own time
        own = segment_minutes(a, b)
        assert w <= own, f"{a.station_code}->{b.station_code}: graph {w} > 12658's own {own}"


def test_12658_edges_are_at_most_its_own_times_and_track_fastest_train(graph, store):
    stops = store.get_real_halt_stops(MAIL)
    for a, b in zip(stops, stops[1:]):
        d = graph[a.station_code][b.station_code]
        if d["train_number"] == MAIL:
            assert d["weight"] == segment_minutes(a, b)
        else:
            assert d["weight"] <= segment_minutes(a, b)
            assert d["train_count"] >= 2


def test_pass_through_station_is_not_on_12658_edge(graph):
    # BNCE (Bangalore East) is a 0-minute pass-through on 12658 between BNC and BWT.
    # The 12658-derived edge must jump straight from BNC to BWT.
    assert graph.has_edge("BNC", "BWT")


def test_minimum_weight_kept_across_trains(graph, store):
    """Rebuild a tiny graph from two synthetic runs and confirm min() semantics."""
    class FakeStore:
        def get_all_train_numbers(self):
            return ["slow", "fast"]

        def get_real_halt_stops(self, tn):
            if tn == "slow":
                return [_stop("X", None, "10:00:00", 1, 0), _stop("Y", "12:00:00", None, 1, 1)]
            return [_stop("X", None, "10:00:00", 1, 0), _stop("Y", "11:00:00", None, 1, 1)]

    g = build_graph(FakeStore(), verbose=False)  # type: ignore[arg-type]
    assert g["X"]["Y"]["weight"] == 60
    assert g["X"]["Y"]["train_number"] == "fast"
    assert g["X"]["Y"]["train_count"] == 2


def test_nonpositive_and_missing_segments_are_skipped():
    class FakeStore:
        def get_all_train_numbers(self):
            return ["bad"]

        def get_real_halt_stops(self, tn):
            return [
                _stop("P", None, "10:00:00", 1, 0),
                _stop("Q", "10:00:00", "10:02:00", 1, 1),   # zero-minute segment P->Q
                _stop("R", None, None, 1, 2),                # missing arrival Q->R
                _stop("S", "09:00:00", None, 1, 3),          # R has no departure
            ]

    g = build_graph(FakeStore(), verbose=False)  # type: ignore[arg-type]
    assert g.number_of_edges() == 0
    stats = g.graph["build_stats"]
    assert stats.skipped_nonpositive == 1
    assert stats.skipped_missing_time == 2
    assert stats.trains_contributing == 0
