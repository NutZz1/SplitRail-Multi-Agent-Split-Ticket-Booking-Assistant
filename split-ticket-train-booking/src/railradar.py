"""RailRadar provider: trains between stations, plus live running status.

RailRadar (https://railradar.in) is a keyed third-party API over Indian
Railways data. It is not an official Indian Railways service -- the official
platform, CRIS Pravah, is restricted to partner organisations -- but unlike
the erail.in scraper it offers a documented JSON contract, a support contact,
and live delay information.

Set ``RAILRADAR_API_KEY`` to enable it. Without a key this module reports
itself unconfigured and callers fall back to erail.in and then to the bundled
timetable. The free plan allows 1,000 requests a month, so treat every call
as scarce: this is a fallback chain, not a cache-free data source.

Responses use the envelope ``{"success": bool, "data": ..., "meta": ...}``;
errors carry ``{"error": {"code", "message"}}``.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date as date_cls
from typing import Optional

from src.live_trains import LiveLookupError, LiveTrain, day_mask

BASE_URL = "https://api.railradar.in/v1"
API_KEY_ENV = "RAILRADAR_API_KEY"
PROVIDER = "railradar.in"


def api_key() -> Optional[str]:
    return os.environ.get(API_KEY_ENV) or None


def is_configured() -> bool:
    return api_key() is not None


@dataclass(frozen=True)
class LiveStatus:
    """Where a train is right now, and how late it is running."""

    train_number: str
    train_name: str
    status: Optional[str]
    delay_minutes: Optional[int]
    current_station: Optional[str]
    next_halt: Optional[str]
    last_updated: Optional[str]

    @property
    def is_on_time(self) -> bool:
        return self.delay_minutes is not None and self.delay_minutes <= 0

    def summary(self) -> str:
        if self.delay_minutes is None:
            return "Running status unavailable"
        if self.delay_minutes <= 0:
            return "On time"
        hours, minutes = divmod(self.delay_minutes, 60)
        late = f"{hours}h {minutes:02d}m" if hours else f"{minutes} min"
        return f"Running {late} late"


def _request(path: str, params: dict) -> dict:
    key = api_key()
    if not key:
        raise LiveLookupError(f"{API_KEY_ENV} is not set.")
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    url = f"{BASE_URL}{path}" + (f"?{query}" if query else "")
    request = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
        "User-Agent": "SplitRail/1.0 (academic project)",
    })
    try:
        with urllib.request.urlopen(request, timeout=8.0) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        # The body carries a useful message; never leak the key into it.
        try:
            detail = json.loads(exc.read().decode())["error"]["message"]
        except Exception:
            detail = exc.reason
        raise LiveLookupError(f"RailRadar refused the request ({exc.code}): {detail}") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise LiveLookupError(f"Could not reach RailRadar: {exc}") from exc

    if not payload.get("success"):
        message = (payload.get("error") or {}).get("message", "unknown error")
        raise LiveLookupError(f"RailRadar returned an error: {message}")
    return payload.get("data") or {}


def _to_live_train(entry: dict, origin: str, destination: str) -> Optional[LiveTrain]:
    train = entry.get("train") or {}
    start, end = entry.get("from") or {}, entry.get("to") or {}
    number, departure, arrival = train.get("number"), start.get("departure"), end.get("arrival")
    if not (number and departure and arrival):
        return None

    duration = entry.get("duration")
    hours, minutes = divmod(int(duration), 60) if duration else (0, 0)
    live = entry.get("live") or {}
    return LiveTrain(
        number=str(number), name=train.get("name") or str(number),
        from_code=origin, from_name=origin, to_code=destination, to_name=destination,
        departure=departure[:5], arrival=arrival[:5],
        travel_time=f"{hours:02d}:{minutes:02d}" if duration else "",
        running_days=day_mask(train.get("runDays")),
        train_origin_code=origin, train_dest_code=destination,
        halts=entry.get("totalHaltsBetween"), distance_km=entry.get("distance"),
        delay_minutes=live.get("delayMinutes"), provider=PROVIDER,
    )


def fetch_between_stations(origin: str, destination: str,
                           when: Optional[date_cls] = None,
                           by_city: bool = False) -> list[LiveTrain]:
    """Trains running ``origin`` -> ``destination``.

    ``by_city`` mirrors erail's habit of widening a search to every station in
    the same metropolitan area. It defaults to off here, so results are the
    stations actually asked for.
    """
    data = _request(f"/trains/between/{urllib.parse.quote(origin)}/{urllib.parse.quote(destination)}",
                    {"date": when.isoformat() if when else None,
                     "byCity": "true" if by_city else None})
    trains = [t for t in (_to_live_train(e, origin, destination)
                          for e in data.get("trains") or []) if t]
    if not trains:
        raise LiveLookupError("RailRadar found no direct trains for this pair.")
    return trains


def fetch_live_status(train_number: str, when: Optional[date_cls] = None) -> LiveStatus:
    """Where ``train_number`` is now, and its current delay."""
    data = _request(f"/trains/{urllib.parse.quote(train_number)}/live",
                    {"date": when.isoformat() if when else None, "haltsOnly": "true"})
    location = data.get("currentLocation") or {}
    next_halt = data.get("nextHalt") or {}
    return LiveStatus(
        train_number=str(data.get("trainNumber") or train_number),
        train_name=data.get("trainName") or "",
        status=data.get("status"), delay_minutes=data.get("delayMinutes"),
        current_station=location.get("stationCode"),
        next_halt=next_halt.get("stationName") or next_halt.get("stationCode"),
        last_updated=data.get("lastUpdatedAt"),
    )
