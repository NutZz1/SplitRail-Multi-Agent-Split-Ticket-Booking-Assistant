"""
Real distances from real station coordinates.

Two notions of distance:

* :func:`segment_distance_km` -- straight-line (great-circle) distance
  between two stations. Cheap, but underestimates track distance badly on
  winding routes (SBC->MAS: 293 km straight vs 362 km real track).
* :func:`route_distance_km` -- the haversine sum along a train's scheduled
  stops between two stations (SBC->MAS on 12658: 347 km). This is what the
  fare model uses; it tracks the real distance far more closely.

293 of the 8,990 stations have no coordinates. ``segment_distance_km``
returns ``None`` if either endpoint lacks them; ``route_distance_km``
bridges over coordinate-less intermediate stops (the README's "safe to
drop" guidance) and returns ``None`` only when an endpoint lacks them.
"""

from __future__ import annotations

import math

from src.data_store import RailDataStore, Station

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lon points in kilometres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def _located(store: RailDataStore, code: str) -> Station | None:
    s = store.get_station(code)
    return s if s is not None and s.has_coordinates else None


def segment_distance_km(station_a: str, station_b: str, store: RailDataStore) -> float | None:
    """Straight-line distance between two stations, or None if either lacks coordinates."""
    a, b = _located(store, station_a), _located(store, station_b)
    if a is None or b is None:
        return None
    return haversine_km(a.lat, a.lon, b.lat, b.lon)  # type: ignore[arg-type]


def route_distance_km(train_number: str, from_station: str, to_station: str, store: RailDataStore) -> float | None:
    """Haversine sum along ``train_number``'s scheduled stops from ``from_station`` to ``to_station``.

    Includes pass-through stops, so it follows the track rather than the
    crow. Intermediate stops without coordinates are skipped (the sum
    bridges straight over them). Returns None if either endpoint is not on
    the route or has no coordinates.
    """
    codes = [s.station_code for s in store.get_stops(train_number)]
    if from_station not in codes or to_station not in codes:
        return None
    i, j = codes.index(from_station), codes.index(to_station)
    if i >= j:
        return None
    located = [_located(store, c) for c in codes[i:j + 1]]
    if located[0] is None or located[-1] is None:
        return None
    points = [s for s in located if s is not None]
    return sum(
        haversine_km(a.lat, a.lon, b.lat, b.lon)  # type: ignore[arg-type]
        for a, b in zip(points, points[1:])
    )
