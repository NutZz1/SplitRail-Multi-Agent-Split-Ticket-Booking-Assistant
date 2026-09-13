"""
Synthetic seat availability generator.

WHY THIS IS SYNTHETIC (and everything else in this package is real):
IRCTC has no free public API, and live seat availability cannot be scraped
in bulk without an authenticated session. No dataset anywhere publishes
real-time berth-level availability. This is the one layer of the project
that must be fabricated -- everything else (stations, coordinates, routes,
halt times, train classes, coach composition) is sourced from real data.

This script seeds availability onto the REAL trains/coaches/segments
already loaded from schedules_clean.json and coach_compositions.json,
so the fabricated part sits on top of a real backbone instead of a fully
invented network.

Usage:
    python generate_seat_availability.py
Produces:
    seat_availability.json
"""

import json
import random

random.seed(42)  # reproducible for grading/demo purposes

STATUS_WEIGHTS = {
    "CONFIRMED": 0.55,
    "RAC": 0.15,
    "WAITLIST": 0.20,
    "UNAVAILABLE": 0.10,
}

def sample_status():
    r = random.random()
    cum = 0
    for status, w in STATUS_WEIGHTS.items():
        cum += w
        if r <= cum:
            return status
    return "UNAVAILABLE"


def generate(coach_compositions_path, schedules_clean_path, out_path, trains_subset=None):
    coach_data = json.load(open(coach_compositions_path))
    schedules = json.load(open(schedules_clean_path))

    availability = {}

    train_numbers = trains_subset or list(coach_data.keys())

    for tn in train_numbers:
        if tn not in coach_data or tn not in schedules:
            continue

        composition = coach_data[tn]["composition"]
        # only berth-bearing coaches get availability entries
        berth_coaches = [c for c in composition if c not in
                         ("EOG", "SLR", "GEN", "PC", "GS")]

        # Only stations where the train actually halts (or the two termini)
        # are realistic boarding/alighting points -- passengers cannot book
        # a ticket to/from a pure technical pass-through stop. This also
        # keeps the generated file a manageable size.
        all_stops = schedules[tn]
        bookable = [
            s["station_code"] for idx, s in enumerate(all_stops)
            if idx == 0 or idx == len(all_stops) - 1
            or (s["halt_minutes"] is not None and s["halt_minutes"] > 0)
        ]
        stops = bookable

        train_avail = {}
        # Generate availability for every (origin, destination) pair on the
        # route -- not just adjacent stops -- since a real ticket segment can
        # span any two stops in order (e.g. a passenger boards at stop 3 and
        # alights at stop 40). This is what the split-ticket search actually
        # needs to query.
        for i in range(len(stops)):
            for j in range(i + 1, len(stops)):
                from_station = stops[i]
                to_station = stops[j]
                seg_key = f"{from_station}-{to_station}"
                train_avail[seg_key] = {
                    coach: sample_status() for coach in berth_coaches
                }

        availability[tn] = train_avail

    with open(out_path, "w") as f:
        json.dump(availability, f, indent=1)

    print(f"Generated availability for {len(availability)} trains -> {out_path}")


if __name__ == "__main__":
    generate(
        coach_compositions_path="coach_compositions.json",
        schedules_clean_path="schedules_clean.json",
        out_path="seat_availability.json",
    )
