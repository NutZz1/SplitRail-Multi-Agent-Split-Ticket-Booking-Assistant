"""Convert the raw Indian Railways schedule dump into ``schedules_clean.json``.

The raw file is a flat array of stop records; this groups them by train and
normalises them into the shape ``build_sqlite_db.py`` expects:

    {"12658": [{"station_code", "station_name", "arrival",
                "departure", "day", "halt_minutes"}, ...], ...}

Usage:

    python data_source/convert_schedules.py ~/Downloads/schedules.json

Route order comes from the raw ``id`` column, which is ascending within a
train. Missing times arrive as the string ``"None"`` and become ``null``.
``halt_minutes`` is derived from arrival/departure and is ``null`` at termini
(where one of the two is missing), ``0`` for a technical pass-through.
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_OUT = HERE / "schedules_clean.json"


def _time(value):
    """Raw missing times are the string "None"; everything else is HH:MM:SS."""
    return None if value in (None, "None") else value


def _minutes(hms):
    h, m, s = (int(p) for p in hms.split(":"))
    return h * 60 + m + (1 if s >= 30 else 0)


def halt_minutes(arrival, departure):
    """Wall-clock halt, wrapping across midnight. None unless both times exist."""
    if arrival is None or departure is None:
        return None
    return float((_minutes(departure) - _minutes(arrival)) % (24 * 60))


def convert(raw_path: Path) -> dict[str, list[dict]]:
    records = json.loads(raw_path.read_text())
    grouped: dict[str, list[dict]] = collections.defaultdict(list)
    for r in records:
        grouped[r["train_number"]].append(r)

    schedules = {}
    for train_number, rows in grouped.items():
        rows.sort(key=lambda r: r["id"])  # raw id encodes route order
        stops = []
        for r in rows:
            arrival, departure = _time(r["arrival"]), _time(r["departure"])
            stops.append({
                "station_code": r["station_code"],
                "station_name": r["station_name"],
                "arrival": arrival,
                "departure": departure,
                "day": r["day"],
                "halt_minutes": halt_minutes(arrival, departure),
            })
        schedules[train_number] = stops
    return schedules


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("raw", type=Path, help="raw schedules.json dump")
    ap.add_argument("-o", "--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    schedules = convert(args.raw)
    args.out.write_text(json.dumps(schedules))
    stops = sum(len(v) for v in schedules.values())
    untimed = sum(1 for v in schedules.values() for s in v
                  if s["arrival"] is None and s["departure"] is None)
    print(f"trains:  {len(schedules):>8,}")
    print(f"stops:   {stops:>8,}")
    print(f"untimed: {untimed:>8,}  (no arrival and no departure)")
    print(f"wrote {args.out} ({args.out.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
