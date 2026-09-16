"""Current trains between two stations, read live from erail.in.

The bundled timetable is a 2020 snapshot, so it misses trains introduced
since and keeps ones since withdrawn. This module asks erail.in for the
services running *today* instead. It is an optional enhancement: every caller
must be able to fall back to the local database, because this depends on a
third party that can be slow, unreachable, or changed without notice.

Response format
---------------
``getTrains.aspx`` answers with a ``~``-delimited text blob rather than JSON.
Records are separated by ``~~~~~~~~`` and each train's fields follow a ``~^``
marker. Field order is positional (see :func:`_parse_train`).

``running_days`` is a seven-character mask of ``1``/``0``. Index 0 is Monday:
train 12008 returns ``1110111`` and is documented as running six days a week
except Thursday, which is index 3. Note this differs from the day mapping in
the AniCrad/indian-rail-api project, which treats index 0 as Wednesday.

The mask is reported *for the boarding station*, not the train's own origin,
so an overnight train shows the day a passenger actually boards.
"""
from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date as date_cls
from typing import Optional

ENDPOINT = "https://erail.in/rail/getTrains.aspx"
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

# Identify the project honestly rather than disguising the request.
USER_AGENT = "SplitRail/1.0 (academic project; +https://github.com/NutZz1/SplitRail-Multi-Agent-Split-Ticket-Booking-Assistant)"

# Messages erail returns in place of results.
_ERRORS = {
    "No direct trains found": "No direct train runs between these stations.",
    "From station not found": "The origin station code was not recognised.",
    "To station not found": "The destination station code was not recognised.",
    "Please try again after some time.": "The live timetable service is busy.",
}


class LiveLookupError(RuntimeError):
    """The live service could not answer. Callers should fall back."""


@dataclass(frozen=True)
class LiveTrain:
    """One currently-running service between the requested stations."""

    number: str
    name: str
    from_code: str
    from_name: str
    to_code: str
    to_name: str
    departure: str          # HH:MM at from_code
    arrival: str            # HH:MM at to_code
    travel_time: str        # HH:MM elapsed
    running_days: str       # 7 chars, index 0 = Monday
    train_origin_code: str  # where the train itself starts
    train_dest_code: str    # where the train itself ends

    @property
    def duration_minutes(self) -> Optional[int]:
        try:
            h, m = self.travel_time.split(":")
            return int(h) * 60 + int(m)
        except ValueError:
            return None

    @property
    def runs_daily(self) -> bool:
        return self.running_days == "1111111"

    def runs_on(self, when: date_cls) -> bool:
        """Does this service run on ``when``? ``date.weekday()`` is Monday=0."""
        return self.running_days[when.weekday()] == "1"

    def running_day_names(self) -> list[str]:
        return [WEEKDAYS[i] for i, flag in enumerate(self.running_days) if flag == "1"]


def _clock(value: str) -> str:
    """erail writes times as ``04.35``; normalise to ``04:35``."""
    return value.replace(".", ":") if value else value


def _parse_train(fields: list[str]) -> Optional[LiveTrain]:
    if len(fields) < 14:
        return None
    return LiveTrain(
        number=fields[0], name=fields[1],
        train_origin_code=fields[3], train_dest_code=fields[5],
        from_name=fields[6], from_code=fields[7],
        to_name=fields[8], to_code=fields[9],
        departure=_clock(fields[10]), arrival=_clock(fields[11]),
        travel_time=_clock(fields[12]), running_days=fields[13],
    )


def parse_between_stations(payload: str) -> list[LiveTrain]:
    """Turn a raw ``getTrains.aspx`` body into :class:`LiveTrain` records.

    Split out from the network call so it can be tested against saved
    responses without touching the network.
    """
    blocks = [b for b in payload.split("~~~~~~~~") if b]
    if not blocks:
        raise LiveLookupError("The live timetable returned an empty response.")

    head = blocks[0].replace("~", "").split("<")[0].strip()
    for marker, message in _ERRORS.items():
        if head.startswith(marker):
            raise LiveLookupError(message)

    trains = []
    for block in blocks:
        parts = block.split("~^")
        if len(parts) != 2:
            continue
        train = _parse_train([f for f in parts[1].split("~") if f])
        if train is not None:
            trains.append(train)
    if not trains:
        raise LiveLookupError("The live timetable returned no usable records.")
    return trains


def fetch_between_stations(origin: str, destination: str, timeout: float = 8.0) -> list[LiveTrain]:
    """Current services ``origin`` -> ``destination``, newest timetable.

    Raises :class:`LiveLookupError` for anything the caller should treat as
    "fall back to the local timetable": network failure, a slow response, or
    one of erail's own error messages.

    erail widens a query to nearby stations in the same city, so a request for
    SBC can return services from SMVB or YPR. Each record therefore carries
    its own ``from_code``/``to_code``; do not assume they match the request.
    """
    query = urllib.parse.urlencode({
        "Station_From": origin, "Station_To": destination,
        "DataSource": 0, "Language": 0, "Cache": "true",
    })
    request = urllib.request.Request(f"{ENDPOINT}?{query}", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LiveLookupError(f"Could not reach the live timetable: {exc}") from exc
    return parse_between_stations(payload)
