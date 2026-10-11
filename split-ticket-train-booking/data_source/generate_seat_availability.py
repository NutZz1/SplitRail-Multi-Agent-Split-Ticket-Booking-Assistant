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

The model (why some trains/segments read "almost full" and others "medium")
--------------------------------------------------------------------------
A flat, identical distribution for every train/segment/coach (the previous
version of this script) reads as noise, not a railway: real charts are not
i.i.d. Three real-world effects are modelled instead, combined into one
"fill pressure" per (train, segment, coach), which then drives the status
weights:

* **Per-train demand** (``TRAIN_DEMAND``) -- some services are simply more
  booked than others (an overnight mail/express on a single-line corridor
  vs. a same-day intercity run). This is a route-character judgement, not
  fabricated ridership data.
* **Per-class demand** (``CLASS_DEMAND_FACTOR``) -- Sleeper fills first and
  hardest; the AC tiers, being pricier, run comparatively easier; chair car
  sits in between. This mirrors the real IRCTC pattern where SL waitlists
  balloon while 2A/1A on the same train still shows availability.
* **Depth into the route** -- a berth sold near the train's origin has had
  fewer opportunities to be taken than the same coach further down the
  line, because upstream passengers riding through are still occupying it.
  Fill pressure therefore rises with how far ``from_station`` is into the
  stop sequence, not just with the segment length.

Per-coach noise on top keeps neighbouring bays from reading as identical.

Usage:
    python generate_seat_availability.py
Produces:
    seat_availability.json
"""

import hashlib
import json
import random

random.seed(42)  # reproducible for grading/demo purposes

#: Coach-code prefix -> travel class, mirroring src.data_store.COACH_CLASS_PREFIXES
#: (duplicated rather than imported: this script runs standalone, outside the
#: package, against raw JSON only).
COACH_CLASS_PREFIXES = (
    ("BE", "3A"),
    ("S", "SL"),
    ("B", "3A"),
    ("A", "2A"),
    ("H", "1A"),
    ("D", "CC"),
    ("C", "CC"),
    ("E", "EC"),
)


def coach_class(coach_code):
    for prefix, cls in COACH_CLASS_PREFIXES:
        if coach_code.startswith(prefix) and coach_code[len(prefix):].isdigit():
            return cls
    return None


#: How booked each demo train typically runs, 0 (empty) .. 1+ (overbooked).
#: A route-character judgement (corridor popularity, day/night run, single
#: vs. multiple daily services) rather than fabricated ridership figures.
TRAIN_DEMAND = {
    "12658": 0.60,  # Bangalore - Chennai Mail: steady overnight corridor traffic
    "12609": 0.50,  # Chennai - Bangalore Intercity (day, chair car): moderate
    "16525": 0.68,  # Kanniyakumari - Bangalore Island Exp: long Kerala/Karnataka overnighter, busy
    "12801": 0.80,  # Puri - New Delhi Purushottam Exp: long-haul mail, historically packed
    "15017": 0.55,  # LTT - Gorakhpur Exp: medium
    "12217": 0.88,  # Kochuveli - Chandigarh Kerala Sampark Kranti Exp: famously one of the busiest Kerala trains
}


def _default_demand(train_number):
    """A stable ~0.35..0.95 demand level for a train with no hand-judged
    entry above, derived from the train number rather than a flat constant.

    Once the Kerala corridor grew past a handful of hand-picked trains
    (see coach_compositions.json / schedules_clean.json), judging "how busy
    does this one run" individually for every real train stopped being
    honest -- nobody looked at each one. A flat default would make all of
    them read identically busy instead, which is its own kind of wrong. A
    hash keeps each train's demand fixed and different from its neighbours
    without pretending to be a researched judgement.
    """
    digest = hashlib.sha256(f"demand:{train_number}".encode()).hexdigest()
    return 0.35 + (int(digest[:8], 16) % 1000) / 1000 * 0.6

#: Sleeper fills first and hardest; AC tiers run comparatively easier.
CLASS_DEMAND_FACTOR = {
    "SL": 1.15,
    "3A": 0.90,
    "2A": 0.72,
    "1A": 0.50,
    "CC": 0.85,
    "EC": 0.60,
    "2S": 1.20,
}
DEFAULT_CLASS_FACTOR = 1.0


def status_for_fill(fill):
    """Map fill pressure (roughly 0..1, can exceed 1 for an overbooked run) to a status.

    Weights are unnormalised and sampled proportionally, so they do not need
    to sum to 1 -- higher fill both raises WAITLIST/UNAVAILABLE and lowers
    CONFIRMED at the same time, which is what "almost full" should feel
    like rather than a single knob moving in isolation.
    """
    fill = max(0.0, min(1.2, fill))
    # CONFIRMED last: it is by far the most common outcome, so it is the
    # right bucket to absorb the high tail of the draw below.
    weights = {
        "RAC": 0.10 + 0.05 * fill,
        "WAITLIST": min(0.78, fill * 0.85),
        "UNAVAILABLE": 0.005 + max(0.0, fill - 0.6) ** 2 * 0.9,
        "CONFIRMED": max(0.04, 0.92 - fill * 0.85),
    }
    total = sum(weights.values())
    r = random.random() * total
    cum = 0.0
    for status, w in weights.items():
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
        n = len(stops)
        demand = TRAIN_DEMAND[tn] if tn in TRAIN_DEMAND else _default_demand(tn)

        train_avail = {}
        # Generate availability for every (origin, destination) pair on the
        # route -- not just adjacent stops -- since a real ticket segment can
        # span any two stops in order (e.g. a passenger boards at stop 3 and
        # alights at stop 40). This is what the split-ticket search actually
        # needs to query.
        for i in range(n):
            # How far into the run boarding happens here -- 0 at the origin,
            # 1 at the far terminus. Upstream passengers riding through mean
            # a berth sold deep into the route has less real headroom left
            # than the same coach sold at the origin.
            depth = i / max(1, n - 1)
            for j in range(i + 1, n):
                from_station = stops[i]
                to_station = stops[j]
                seg_key = f"{from_station}-{to_station}"
                coach_status = {}
                for coach in berth_coaches:
                    cls = coach_class(coach) or "SL"
                    class_factor = CLASS_DEMAND_FACTOR.get(cls, DEFAULT_CLASS_FACTOR)
                    fill = demand * class_factor * (0.45 + 0.55 * depth)
                    fill += random.uniform(-0.1, 0.1)  # per-coach noise: bays don't fill in lockstep
                    coach_status[coach] = status_for_fill(fill)
                train_avail[seg_key] = coach_status

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
