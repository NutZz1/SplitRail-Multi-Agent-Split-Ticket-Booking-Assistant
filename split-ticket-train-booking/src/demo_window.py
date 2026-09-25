"""
The rolling booking window.

Real Indian Railways reservation opens a fixed number of days ahead (60 at
the time of writing) and moves forward every day: the window is always
"today .. today + N", never a range frozen in the past. This project used a
hard-coded 2026-09-10 .. 2026-09-25 window baked into
``train_run_calendar.json``, which meant every search started failing the
day that window ran out.

Here the window is computed instead of stored. ``railway.db`` is rebuilt
whenever its recorded ``window_start`` is no longer today, so run dates
always cover ``today .. today + WINDOW_DAYS - 1``.

``WINDOW_DAYS`` is 10 rather than the real 60 because seat availability is
synthetic and generated per running date: ten days is enough to demonstrate
a rolling window, a per-date booking ledger, and "booked on day 3, days
4-10 unaffected", without a sixfold data set.

Pinning the window
------------------
``SPLITRAIL_WINDOW_START=YYYY-MM-DD`` overrides "today". The test suite sets
it so its assertions can name concrete dates, and a demo can use it to get a
reproducible window. Nothing else should set it.
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

    ``runs_on`` is the train's real day-of-week pattern (``["MON", "WED"]``);
    this expands it onto the current window. A train running daily yields
    every date in the window, one running Tue/Fri yields two or three.
    """
    wanted = {d.strip().upper() for d in runs_on}
    return [d.isoformat() for d in window_dates(start, days) if weekday_code(d) in wanted]
