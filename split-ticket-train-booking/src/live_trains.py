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

# Messages erail returns in place of results, and which the caller must treat
# as a FAILURE (fall back to another source).
_ERRORS = {
    "From station not found": "The origin station code was not recognised.",
    "To station not found": "The destination station code was not recognised.",
    "Please try again after some time.": "The live timetable service is busy.",
}

# "No direct trains found" is not in the table above on purpose. It is erail
# ANSWERING -- this pair has no direct service -- not erail failing. Raising
# here would make the caller fall back to the bundled 2020 snapshot and show
# trains that may no longer run, which is the opposite of what the fallback
# chain is for. RailRadar already returns an empty list in the same
# situation; this keeps the two providers consistent.
_NO_TRAINS = "No direct trains found"


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
    # Fields no provider is required to supply.
    halts: Optional[int] = None          # intermediate halts on this leg
    distance_km: Optional[float] = None
    provider: str = "erail.in"

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


def day_mask(days) -> str:
    """Normalise a provider's running-days field to a Monday-first mask.

    Accepts a 7-character ``1``/``0`` string (already canonical) or a list of
    day names in any capitalisation or length, e.g. ``["Mon", "Tuesday"]``.
    Anything unrecognised yields ``"1111111"``, so a train is shown rather
    than silently dropped by a date filter.
    """
    if isinstance(days, str) and len(days) == 7 and set(days) <= {"0", "1"}:
        return days
    if isinstance(days, (list, tuple, set)):
        wanted = {str(d).strip().lower()[:3] for d in days}
        mask = "".join("1" if name.lower()[:3] in wanted else "0" for name in WEEKDAYS)
        if "1" in mask:
            return mask
    return "1111111"


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


def _parse_header(block: str) -> tuple[str, str, str, str]:
    """``(origin_code, origin_name, destination_code, destination_name)``.

    erail echoes the stations it resolved the request to. That resolution is
    what makes renamed stations work: asking for ``BCT`` comes back as
    ``Mumbai Central``, the name its records now carry under ``MMCT``.
    """
    fields = [f for f in block.split("~") if f]
    if len(fields) < 4:
        raise LiveLookupError("The live timetable returned an unreadable response.")
    return fields[0], fields[1], fields[2], fields[3]


def _serves(train: LiveTrain, code: str, name: str, *, at_origin: bool) -> bool:
    """Does this record really start (or end) at the station that was asked for?

    Matches on the resolved station *name* first, so a renamed station still
    matches, and falls back to the code.
    """
    got_code = train.from_code if at_origin else train.to_code
    got_name = train.from_name if at_origin else train.to_name
    return got_code == code or got_name.strip().lower() == name.strip().lower()


def parse_between_stations(payload: str, origin: Optional[str] = None,
                           destination: Optional[str] = None) -> list[LiveTrain]:
    """Turn a raw ``getTrains.aspx`` body into :class:`LiveTrain` records.

    When ``origin`` and ``destination`` are given, records are narrowed to
    trains that genuinely run between those two stations. erail otherwise
    widens a search to every station in the same city, which is badly
    misleading: a request for NDLS -> BCT comes back with 33 trains, of which
    32 run from Hazrat Nizamuddin or into Bandra Terminus instead.

    An empty list is a valid answer -- "no direct train runs this pair" -- and
    is not an error. Only a malformed or refused response raises.

    Split out from the network call so it can be tested against saved
    responses without touching the network.
    """
    blocks = [b for b in payload.split("~~~~~~~~") if b]
    if not blocks:
        raise LiveLookupError("The live timetable returned an empty response.")

    head = blocks[0].replace("~", "").split("<")[0].strip()
    if head.startswith(_NO_TRAINS):
        return []  # an answer, not a failure -- see _NO_TRAINS above
    for marker, message in _ERRORS.items():
        if head.startswith(marker):
            raise LiveLookupError(message)

    origin_code, origin_name, dest_code, dest_name = _parse_header(blocks[0])

    trains = []
    for block in blocks:
        parts = block.split("~^")
        if len(parts) != 2:
            continue
        train = _parse_train([f for f in parts[1].split("~") if f])
        if train is not None:
            trains.append(train)

    if origin is not None and destination is not None:
        trains = [t for t in trains
                  if _serves(t, origin_code, origin_name, at_origin=True)
                  and _serves(t, dest_code, dest_name, at_origin=False)]
    return trains


def fetch_between_stations(origin: str, destination: str, timeout: float = 8.0) -> list[LiveTrain]:
    """Current services ``origin`` -> ``destination``, newest timetable.

    Raises :class:`LiveLookupError` for anything the caller should treat as
    "fall back to the local timetable": network failure, a slow response, or
    one of erail's own error messages.

    Results are narrowed to trains that actually run between the two stations
    asked for; erail otherwise widens the search to the whole city. An empty
    list means no direct train runs the pair, which is an answer rather than
    a failure.
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
    return parse_between_stations(payload, origin, destination)
