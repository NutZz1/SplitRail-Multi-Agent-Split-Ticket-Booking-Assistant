"""
The rolling booking window.

The old data set froze its run dates at 2026-09-10 .. 2026-09-25, so every
search started failing on 2026-09-26. These tests pin the clock explicitly
and move it, rather than waiting for the calendar, so the rolling behaviour
is checked rather than assumed.
"""

from __future__ import annotations

import datetime as dt

import pytest

from src.data_store import RailDataStore
from src.demo_scenarios import ensure_database, validate_date, weekday_in_window
from src.demo_window import (
    WINDOW_DAYS,
    WINDOW_START_ENV,
    running_dates,
    weekday_code,
    window_bounds,
    window_dates,
    window_start,
)


@pytest.fixture
def unpinned(monkeypatch):
    """Remove the suite-wide pin so 'today' really means today."""
    monkeypatch.delenv(WINDOW_START_ENV, raising=False)


# --------------------------------------------------------------------------- #
# Window arithmetic
# --------------------------------------------------------------------------- #
def test_window_is_ten_days_starting_today(unpinned):
    first, last = window_bounds()
    assert first == dt.date.today()
    assert (last - first).days == WINDOW_DAYS - 1
    assert len(window_dates()) == WINDOW_DAYS


def test_window_rolls_forward_with_the_clock(monkeypatch):
    """Tomorrow's window starts tomorrow. This is the whole fix."""
    monkeypatch.setenv(WINDOW_START_ENV, "2026-09-25")
    today = window_bounds()
    monkeypatch.setenv(WINDOW_START_ENV, "2026-09-26")
    tomorrow = window_bounds()
    assert today == (dt.date(2026, 9, 25), dt.date(2026, 10, 4))
    assert tomorrow == (dt.date(2026, 9, 26), dt.date(2026, 10, 5))
    assert tomorrow[0] == today[0] + dt.timedelta(days=1)
    assert tomorrow[1] == today[1] + dt.timedelta(days=1)


def test_pin_overrides_today(monkeypatch):
    monkeypatch.setenv(WINDOW_START_ENV, "2027-01-15")
    assert window_start() == dt.date(2027, 1, 15)


def test_a_bad_pin_is_reported_not_ignored(monkeypatch):
    monkeypatch.setenv(WINDOW_START_ENV, "15-01-2027")
    with pytest.raises(ValueError, match=WINDOW_START_ENV):
        window_start()


def test_every_weekday_appears_in_a_ten_day_window(unpinned):
    """Ten days > seven, so a weekday-anchored scenario can always be built."""
    codes = {weekday_code(d) for d in window_dates()}
    assert codes == {"MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"}


# --------------------------------------------------------------------------- #
# Expanding a weekday pattern onto the window
# --------------------------------------------------------------------------- #
def test_daily_train_runs_on_every_day_of_the_window(monkeypatch):
    monkeypatch.setenv(WINDOW_START_ENV, "2026-09-10")
    daily = running_dates(["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"])
    assert len(daily) == WINDOW_DAYS
    assert daily[0] == "2026-09-10" and daily[-1] == "2026-09-19"


def test_twice_weekly_train_runs_only_on_its_days(monkeypatch):
    monkeypatch.setenv(WINDOW_START_ENV, "2026-09-10")  # a Thursday
    tue_fri = running_dates(["TUE", "FRI"])
    assert tue_fri == ["2026-09-11", "2026-09-15", "2026-09-18"]
    assert all(weekday_code(dt.date.fromisoformat(d)) in {"TUE", "FRI"} for d in tue_fri)


def test_pattern_is_case_and_whitespace_tolerant(monkeypatch):
    monkeypatch.setenv(WINDOW_START_ENV, "2026-09-10")
    assert running_dates([" tue ", "Fri"]) == running_dates(["TUE", "FRI"])


# --------------------------------------------------------------------------- #
# The database follows the window
# --------------------------------------------------------------------------- #
def test_database_run_dates_match_the_pinned_window(store):
    """conftest pins 2026-09-10, so the built database covers exactly ten days."""
    assert store.get_run_date_range() == ("2026-09-10", "2026-09-19")
    assert store.get_window() == ("2026-09-10", "2026-09-19")
    assert store.get_meta("window_days") == str(WINDOW_DAYS)


def test_a_database_built_for_another_day_is_stale(store, monkeypatch):
    monkeypatch.setenv(WINDOW_START_ENV, "2026-09-10")
    assert store.window_is_stale() is False
    monkeypatch.setenv(WINDOW_START_ENV, "2026-09-11")
    assert store.window_is_stale() is True


def test_ensure_database_rebuilds_a_stale_file(tmp_path, monkeypatch):
    """This is what keeps the demo alive: the window moves, the file follows."""
    db = tmp_path / "railway.db"
    monkeypatch.setenv(WINDOW_START_ENV, "2026-09-10")
    ensure_database(db, verbose=False)
    with RailDataStore(db) as store:
        assert store.get_window() == ("2026-09-10", "2026-09-19")

    monkeypatch.setenv(WINDOW_START_ENV, "2026-09-11")
    ensure_database(db, verbose=False)  # notices the move and rebuilds
    with RailDataStore(db) as store:
        assert store.get_window() == ("2026-09-11", "2026-09-20")
        assert store.runs_on_date("12658", "2026-09-20") is True   # newly in window
        assert store.runs_on_date("12658", "2026-09-10") is False  # dropped off the back


def test_ensure_database_leaves_a_current_file_alone(tmp_path, monkeypatch):
    db = tmp_path / "railway.db"
    monkeypatch.setenv(WINDOW_START_ENV, "2026-09-10")
    ensure_database(db, verbose=False)
    built_at = db.stat().st_mtime_ns
    ensure_database(db, verbose=False)
    assert db.stat().st_mtime_ns == built_at, "a current database must not be rebuilt"


# --------------------------------------------------------------------------- #
# Validation tracks the window rather than a hard-coded range
# --------------------------------------------------------------------------- #
def test_dates_inside_the_window_are_accepted(store):
    for day in range(10, 20):
        assert validate_date(dt.date(2026, 9, day), store) == dt.date(2026, 9, day)


def test_dates_outside_the_window_are_refused(store):
    for bad in (dt.date(2026, 9, 9), dt.date(2026, 9, 20), dt.date(2027, 1, 1)):
        with pytest.raises(Exception, match="booking window"):
            validate_date(bad, store)


def test_scenarios_are_anchored_to_the_window_not_to_september_2026(store):
    """weekday_in_window is why the four demo scenarios never expire again."""
    wednesday = weekday_in_window("WED")
    assert weekday_code(wednesday) == "WED"
    lo, hi = store.get_run_date_range()
    assert lo <= wednesday.isoformat() <= hi
