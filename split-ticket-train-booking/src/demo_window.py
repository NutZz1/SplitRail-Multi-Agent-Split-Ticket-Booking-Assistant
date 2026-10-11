"""Ten-day simulated booking window, checked at application startup.

SPLITRAIL_WINDOW_START=YYYY-MM-DD pins a reproducible demonstration window.
Otherwise it starts on today's local date. Weekday patterns are the local
simulation calendar, not a claim about current Indian Railways operations.
Reference data may be rebuilt when the window moves; bookings are separate.
"""

from __future__ import annotations

import datetime as dt
import os

#: How many days the booking window covers, counting today as day 1.
WINDOW_DAYS: int = 10

#: Environment override for the first day of the window (tests / reproducible demos).
WINDOW_START_ENV: str = "SPLITRAIL_WINDOW_START"

#: Day-of-week codes as ``train_run_pattern`` stores them; index 0 is Monday,
#: matching ``datetime.date.weekday()``.
WEEKDAY_CODES: tuple[str, ...] = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")


def window_start(today: dt.date | None = None) -> dt.date:
    """First day of the booking window: ``today``, or the pinned override."""
    pinned = os.environ.get(WINDOW_START_ENV, "").strip()
    if pinned:
        try:
            return dt.date.fromisoformat(pinned)
        except ValueError:
            raise ValueError(
                f"{WINDOW_START_ENV}={pinned!r} is not a date in YYYY-MM-DD form."
            ) from None
    return today or dt.date.today()


def window_dates(start: dt.date | None = None, days: int = WINDOW_DAYS) -> list[dt.date]:
    """Every date in the window, earliest first (``days`` dates, starting at ``start``)."""
    first = start or window_start()
    return [first + dt.timedelta(days=i) for i in range(days)]


def window_bounds(start: dt.date | None = None, days: int = WINDOW_DAYS) -> tuple[dt.date, dt.date]:
    """``(first, last)`` day of the window, both inclusive."""
    dates = window_dates(start, days)
    return dates[0], dates[-1]


def weekday_code(date: dt.date) -> str:
    """``MON``..``SUN`` for a date, matching ``train_run_pattern.day_of_week``."""
    return WEEKDAY_CODES[date.weekday()]


def running_dates(runs_on: list[str], start: dt.date | None = None,
                  days: int = WINDOW_DAYS) -> list[str]:
    """The dates in the window on which a train with this weekday pattern runs.

    ``runs_on`` is the train's local day-of-week pattern (``["MON", "WED"]``);
    this expands it onto the current window. A train running daily yields
    every date in the window, one running Tue/Fri yields two or three.
    """
    wanted = {d.strip().upper() for d in runs_on}
    return [d.isoformat() for d in window_dates(start, days) if weekday_code(d) in wanted]
