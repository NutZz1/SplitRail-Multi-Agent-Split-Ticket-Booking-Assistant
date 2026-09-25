"""
The TTL cache in front of live timetable lookups.

RailRadar's free plan is 1,000 requests a month and the interface searches on
load, so a repeated lookup must be answered from memory. Time is injected
rather than slept through, so these run instantly and never flake.
"""

from __future__ import annotations

import pytest

from src.live_cache import MISSING, TTLCache


def test_a_stored_value_comes_back():
    cache = TTLCache(ttl_seconds=100)
    cache.set("k", [1, 2, 3], now=0)
    assert cache.get("k", now=10) == [1, 2, 3]
    assert cache.hits == 1 and cache.misses == 0


def test_an_absent_key_is_missing_not_none():
    """None is a legitimate cached value, so absence needs its own sentinel."""
    cache = TTLCache()
    assert cache.get("nope") is MISSING
    cache.set("k", None)
    assert cache.get("k") is None


def test_an_entry_expires_after_its_ttl():
    cache = TTLCache(ttl_seconds=100)
    cache.set("k", "v", now=0)
    assert cache.get("k", now=99) == "v"
    assert cache.get("k", now=101) is MISSING


def test_an_expired_entry_is_dropped_not_kept():
    cache = TTLCache(ttl_seconds=10)
    cache.set("k", "v", now=0)
    cache.get("k", now=50)
    assert len(cache) == 0


def test_eviction_caps_the_size():
    cache = TTLCache(ttl_seconds=1000, max_entries=3)
    for i in range(5):
        cache.set(i, i, now=i)
    assert len(cache) == 3
    assert cache.get(0, now=10) is MISSING      # oldest evicted
    assert cache.get(4, now=10) == 4


def test_rewriting_a_key_refreshes_it_without_growing():
    cache = TTLCache(ttl_seconds=100, max_entries=2)
    cache.set("a", 1, now=0)
    cache.set("b", 2, now=1)
    cache.set("a", 3, now=2)     # refresh, not a new entry
    assert len(cache) == 2
    assert cache.get("a", now=3) == 3
    assert cache.get("b", now=3) == 2


def test_clear_resets_entries_and_counters():
    cache = TTLCache()
    cache.set("k", "v")
    cache.get("k")
    cache.get("absent")
    cache.clear()
    assert len(cache) == 0 and cache.hits == 0 and cache.misses == 0


def test_stats_report_the_hit_rate():
    cache = TTLCache()
    assert cache.stats["hit_rate"] == 0.0
    cache.set("k", "v")
    cache.get("k")
    cache.get("k")
    cache.get("absent")
    stats = cache.stats
    assert stats["hits"] == 2 and stats["misses"] == 1
    assert stats["hit_rate"] == pytest.approx(2 / 3, abs=0.001)
    assert stats["entries"] == 1


# --------------------------------------------------------------------------- #
# Integration: the web adapter must not spend the quota twice
# --------------------------------------------------------------------------- #
def test_repeat_lookups_do_not_call_the_provider_again(monkeypatch):
    import web
    from src.live_cache import live_trains_cache

    live_trains_cache.clear()
    calls = []

    class FakeTrain:
        number, name = "12658", "Test Mail"
        from_code, to_code = "SBC", "MAS"
        departure, arrival = "22:45", "04:40"
        duration_minutes, halts, distance_km = 355, 6, 362.0
        provider = "railradar.in"

        def runs_on(self, when):
            return True

        def running_day_names(self):
            return ["Monday"]

    def fake_fetch(origin, destination, when=None):
        calls.append((origin, destination, when))
        return [FakeTrain()]

    monkeypatch.setattr(web.railradar, "is_configured", lambda: True)
    monkeypatch.setattr(web.railradar, "fetch_between_stations", fake_fetch)

    first = web._live_direct("SBC", "MAS", None)
    second = web._live_direct("SBC", "MAS", None)

    assert len(calls) == 1, "the second lookup must be served from the cache"
    assert first[0] == second[0]
    assert second[2].endswith("(cached)"), "the interface should say the answer was cached"

    # A different pair is a different key, so it really does call out again.
    web._live_direct("PURI", "CDG", None)
    assert len(calls) == 2
    live_trains_cache.clear()
