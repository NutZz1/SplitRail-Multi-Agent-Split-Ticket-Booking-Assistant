"""
Build railway.db (SQLite) from the raw JSON files in this folder.

Run from the data_source/ directory:

    python build_sqlite_db.py            # writes ../railway.db
    python build_sqlite_db.py out.db     # writes to a custom path

Re-run this only if the JSON sources change (e.g. after adding a new demo
train to coach_compositions.json and regenerating seat_availability.json).
The resulting railway.db is the single source of truth for the application;
nothing in src/ writes to it.

Schema
------
  stations(code PK, name, state, zone, lat, lon)
  trains(number PK, name, type, from_station_code, from_station_name,
         to_station_code, to_station_name, distance_km, duration_h, duration_m,
         first_ac, second_ac, third_ac, sleeper, chair_car, first_class)
  schedule_stops(id PK, train_number, stop_order, station_code, station_name,
                 arrival, departure, journey_day, halt_minutes)
  coach_compositions(train_number, position, coach_code, rake_type, source_note)
  berth_layout_rules(class_code PK, class_name, berths_per_coach, bay_size, notes)
  seat_availability(train_number, from_station, to_station, coach_code, status)
  train_run_pattern(train_number, day_of_week)
  train_run_dates(train_number, run_date)
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_OUT = HERE.parent / "railway.db"

SCHEMA = """
CREATE TABLE stations (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    state TEXT,
    zone TEXT,
    lat REAL,
    lon REAL
);

CREATE TABLE trains (
    number TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT,
    from_station_code TEXT,
    from_station_name TEXT,
    to_station_code TEXT,
    to_station_name TEXT,
    distance_km INTEGER,
    duration_h INTEGER,
    duration_m INTEGER,
    first_ac INTEGER NOT NULL DEFAULT 0,
    second_ac INTEGER NOT NULL DEFAULT 0,
    third_ac INTEGER NOT NULL DEFAULT 0,
    sleeper INTEGER NOT NULL DEFAULT 0,
    chair_car INTEGER NOT NULL DEFAULT 0,
    first_class INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE schedule_stops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    train_number TEXT NOT NULL,
    stop_order INTEGER NOT NULL,
    station_code TEXT NOT NULL,
    station_name TEXT,
    arrival TEXT,
    departure TEXT,
    journey_day INTEGER,
    halt_minutes REAL,
    UNIQUE (train_number, stop_order)
);
CREATE INDEX idx_schedule_stops_train ON schedule_stops (train_number, stop_order);
CREATE INDEX idx_schedule_stops_station ON schedule_stops (station_code);

CREATE TABLE coach_compositions (
    train_number TEXT NOT NULL,
    position INTEGER NOT NULL,
    coach_code TEXT NOT NULL,
    rake_type TEXT,
    source_note TEXT,
    PRIMARY KEY (train_number, position)
);

CREATE TABLE berth_layout_rules (
    class_code TEXT PRIMARY KEY,
    class_name TEXT NOT NULL,
    berths_per_coach INTEGER,
    bay_size INTEGER,
    notes TEXT
);

CREATE TABLE seat_availability (
    train_number TEXT NOT NULL,
    from_station TEXT NOT NULL,
    to_station TEXT NOT NULL,
    coach_code TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('CONFIRMED', 'RAC', 'WAITLIST', 'UNAVAILABLE')),
    PRIMARY KEY (train_number, from_station, to_station, coach_code)
);

CREATE TABLE train_run_pattern (
    train_number TEXT NOT NULL,
    day_of_week TEXT NOT NULL CHECK (day_of_week IN ('MON','TUE','WED','THU','FRI','SAT','SUN')),
    PRIMARY KEY (train_number, day_of_week)
);

CREATE TABLE train_run_dates (
    train_number TEXT NOT NULL,
    run_date TEXT NOT NULL,
    PRIMARY KEY (train_number, run_date)
);
"""


def _load(name: str):
    with open(HERE / name, encoding="utf-8") as f:
        return json.load(f)


def build(out_path: Path) -> None:
    if out_path.exists():
        os.remove(out_path)

    conn = sqlite3.connect(out_path)
    cur = conn.cursor()
    cur.executescript(SCHEMA)

    # --- stations ---------------------------------------------------------
    stations = _load("stations_clean.json")
    cur.executemany(
        "INSERT INTO stations VALUES (?, ?, ?, ?, ?, ?)",
        (
            (code, s["name"], s.get("state"), s.get("zone"), s.get("lat"), s.get("lon"))
            for code, s in stations.items()
        ),
    )
    print(f"stations:            {len(stations):>8,}")

    # --- trains -----------------------------------------------------------
    trains = _load("trains_clean.json")
    cur.executemany(
        "INSERT INTO trains VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            (
                number,
                t["name"],
                t.get("type"),
                t.get("from_station_code"),
                t.get("from_station_name"),
                t.get("to_station_code"),
                t.get("to_station_name"),
                t.get("distance_km"),
                t.get("duration_h"),
                t.get("duration_m"),
                int(bool(t["classes"].get("first_ac"))),
                int(bool(t["classes"].get("second_ac"))),
                int(bool(t["classes"].get("third_ac"))),
                int(bool(t["classes"].get("sleeper"))),
                int(bool(t["classes"].get("chair_car"))),
                int(bool(t["classes"].get("first_class"))),
            )
            for number, t in trains.items()
        ),
    )
    print(f"trains:              {len(trains):>8,}")

    # --- schedule_stops ---------------------------------------------------
    # A fresh clone includes the six demo timetables; the full data is optional.
    schedules = (_load("schedules_clean.json") if (HERE / "schedules_clean.json").exists()
                 else _load("demo_subset.json")["schedules"])

    def stop_rows():
        for train_number, stops in schedules.items():
            for order, s in enumerate(stops):
                yield (
                    train_number,
                    order,
                    s["station_code"],
                    s.get("station_name"),
                    s.get("arrival"),
                    s.get("departure"),
                    s.get("day"),
                    s.get("halt_minutes"),
                )

    cur.executemany(
        "INSERT INTO schedule_stops (train_number, stop_order, station_code, station_name, "
        "arrival, departure, journey_day, halt_minutes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        stop_rows(),
    )
    n_stops = cur.execute("SELECT COUNT(*) FROM schedule_stops").fetchone()[0]
    print(f"schedule_stops:      {n_stops:>8,}")
    del schedules  # ~65 MB in memory; free it

    # --- coach_compositions -----------------------------------------------
    compositions = _load("coach_compositions.json")
    cur.executemany(
        "INSERT INTO coach_compositions VALUES (?, ?, ?, ?, ?)",
        (
            (train_number, pos, coach, c.get("rake_type"), c.get("source"))
            for train_number, c in compositions.items()
            for pos, coach in enumerate(c["composition"])
        ),
    )
    print(f"coach_compositions:  {len(compositions):>8,} trains")

    # --- berth_layout_rules -----------------------------------------------
    rules = _load("berth_layout_rules.json")
    cur.executemany(
        "INSERT INTO berth_layout_rules VALUES (?, ?, ?, ?, ?)",
        (
            (code, r["name"], r.get("berths_per_coach"), r.get("bay_size"), r.get("notes"))
            for code, r in rules.items()
        ),
    )
    print(f"berth_layout_rules:  {len(rules):>8,}")

    # --- seat_availability ------------------------------------------------
    availability = _load("seat_availability.json")

    def avail_rows():
        for train_number, segments in availability.items():
            for seg_key, coaches in segments.items():
                from_station, to_station = seg_key.split("-", 1)
                for coach, status in coaches.items():
                    yield (train_number, from_station, to_station, coach, status)

    cur.executemany("INSERT INTO seat_availability VALUES (?, ?, ?, ?, ?)", avail_rows())
    n_avail = cur.execute("SELECT COUNT(*) FROM seat_availability").fetchone()[0]
    print(f"seat_availability:   {n_avail:>8,}")

    # --- train_run_pattern / train_run_dates ------------------------------
    calendar = _load("train_run_calendar.json")
    cur.executemany(
        "INSERT INTO train_run_pattern VALUES (?, ?)",
        ((tn, dow) for tn, c in calendar.items() for dow in c["runs_on"]),
    )
    cur.executemany(
        "INSERT INTO train_run_dates VALUES (?, ?)",
        ((tn, d) for tn, c in calendar.items() for d in c["running_dates"]),
    )
    n_dates = cur.execute("SELECT COUNT(*) FROM train_run_dates").fetchone()[0]
    print(f"train_run_pattern:   {len(calendar):>8,} trains")
    print(f"train_run_dates:     {n_dates:>8,}")

    conn.commit()
    conn.execute("VACUUM")
    conn.close()
    print(f"\nWrote {out_path} ({out_path.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    target = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT_OUT
    build(target)
