"""
A small time-to-live cache for live timetable lookups.

RailRadar's free plan allows **1,000 requests a month**, and the interface
runs a search as soon as the page loads -- so a handful of refreshes during a
demo used to cost a handful of requests. Timetables do not change minute to
minute, so answering a repeated origin/destination/date from memory is both
cheaper and faster, and it makes the quota last.

Deliberately in-process and unbounded in time only by ``ttl_seconds``: this
is a single-process demo server, so a dict is the right size of solution.
Nothing here is persisted, and restarting the server starts cold.

Only *successful* lookups are cached. A failure must be retried, because the
fallback chain depends on knowing whether a provider is reachable right now.
"""

from __future__ import annotations

import time
from typing import Any, Hashable

#: How long a cached timetable answer stays fresh. Half an hour is far shorter
#: than the rate at which published timetables change, and short enough that a
#: demo which sets RAILRADAR_API_KEY mid-session sees the new provider quickly.
DEFAULT_TTL_SECONDS: float = 1800.0

#: Cap on distinct keys, so a long-running server cannot grow without bound.
DEFAULT_MAX_ENTRIES: int = 256

_MISSING = object()


class TTLCache:
    """Mapping with per-entry expiry and a hard cap on size.

    Eviction is oldest-inserted-first once :attr:`max_entries` is reached,
    which for this workload (a handful of station pairs) effectively never
    fires -- it exists so a pathological caller cannot exhaust memory.
    """

    def __init__(self, ttl_seconds: float = DEFAULT_TTL_SECONDS,
                 max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._entries: dict[Hashable, tuple[float, Any]] = {}
        self.hits = 0
        self.misses = 0

    def get(self, key: Hashable, now: float | None = None) -> Any:
        """The cached value, or :data:`MISSING` when absent or stale."""
        now = time.monotonic() if now is None else now
        entry = self._entries.get(key)
        if entry is None:
            self.misses += 1
            return _MISSING
        stored_at, value = entry
        if now - stored_at > self.ttl_seconds:
            del self._entries[key]          # expired: drop it rather than keep it around
            self.misses += 1
            return _MISSING
        self.hits += 1
        return value

    def set(self, key: Hashable, value: Any, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        if key in self._entries:
            del self._entries[key]          # re-insert so eviction order is by freshness
        elif len(self._entries) >= self.max_entries:
            oldest = next(iter(self._entries))
            del self._entries[oldest]
        self._entries[key] = (now, value)

    def clear(self) -> None:
        self._entries.clear()
        self.hits = self.misses = 0

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def stats(self) -> dict:
        """Hit/miss counters, for the interface's "behind your results" panel."""
        looked_up = self.hits + self.misses
        return {"entries": len(self._entries), "hits": self.hits, "misses": self.misses,
                "hit_rate": round(self.hits / looked_up, 3) if looked_up else 0.0,
                "ttl_seconds": self.ttl_seconds}


#: The cache the web server uses. Module-level so every request shares it.
MISSING = _MISSING
live_trains_cache = TTLCache()
