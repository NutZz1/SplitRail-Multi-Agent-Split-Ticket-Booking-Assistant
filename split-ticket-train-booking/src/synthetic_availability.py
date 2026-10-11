"""
Synthetic per-class fare + availability for trains OUTSIDE the six demo trains.

data_source/generate_seat_availability.py seeds a realistic, demand-shaped
availability baseline for the six trains that have full schedule and coach
data. The "direct trains on this route" panel, though, lists EVERY real
train that runs a route (from erail.in or the bundled 2020 timetable --
see ``direct_trains()`` in web.py), which is normally 20-40 trains for a
route like Kayankulam -> Palakkad, not six. A listing that only prices six
of them and leaves the rest bare reads as broken, not honest.

This module extends the same idea -- real class offerings and a real
distance, with a synthetic demand layer on top, exactly what
data_source/generate_seat_availability.py already does -- to any real train
number, using only data already in ``railway.db``: the train's own class
flags (``trains`` table) and either erail's reported distance or the
haversine distance between the two stations (both real). No live seat
availability exists to fetch (see the module docstring in
data_source/generate_seat_availability.py for why); what varies here is
which of a real train's classes get priced and how full each one looks.

Deterministic per (train, class, date) via a hash seed, not fresh random
noise on every request: reloading the page shows the same chart until the
date changes, which is closer to how a real IRCTC snapshot behaves than
i.i.d. randomness on every call would be.
"""

from __future__ import annotations

import hashlib
import random

from src.fare_model import (
    CLASS_FACTOR,
    GST_CLASSES,
    GST_RATE,
    RESERVATION_CHARGE,
    SUPERFAST_CHARGE,
    base_fare,
)

#: Real IRCTC waitlist-quota prefixes, used only as flavour text on a
#: synthetic waitlist number -- which quota pool a given berth falls in is
#: not something a free listing can determine.
WAITLIST_QUOTAS: tuple[str, ...] = ("GNWL", "PQWL", "RSWL", "RLWL")

#: Sleeper fills first and hardest; AC tiers run comparatively easier.
#: Mirrors data_source/generate_seat_availability.py's CLASS_DEMAND_FACTOR.
CLASS_DEMAND_FACTOR: dict[str, float] = {
    "SL": 1.15, "3A": 0.90, "2A": 0.72, "1A": 0.50, "CC": 0.85, "EC": 0.60, "2S": 1.20, "FC": 0.65,
}

#: Real published berths-per-coach, from berth_layout_rules.json.
CLASS_CAPACITY: dict[str, int] = {
    "1A": 18, "2A": 46, "3A": 64, "SL": 72, "CC": 78, "EC": 56, "2S": 108, "FC": 18,
}


def _unit_from_hash(*parts: str) -> float:
    """A stable value in [0, 1) derived from ``parts``, via sha256."""
    digest = hashlib.sha256(":".join(parts).encode()).hexdigest()
    return int(digest[:12], 16) / 16 ** 12


def _train_demand(train_number: str) -> float:
    """A stable ~0.35..0.95 'how booked does this train run' factor.

    Derived from the train number, not real ridership data (which does not
    exist for a free demo) -- the point is realistic-looking VARIATION
    across trains (some read as packed, some as roomy), not a true figure.
    """
    return 0.35 + _unit_from_hash("demand", train_number) * 0.6


def class_snapshot(train_number: str, class_code: str, km: float | None, date: str) -> dict | None:
    """One class's synthetic fare + availability box for one real train.

    Returns None when the class or distance can't be priced (mirrors
    src.fare_model.fare_breakdown's own None case), so a caller can simply
    filter blanks out of the class list it builds.
    """
    if class_code not in CLASS_FACTOR or not km or km <= 0:
        return None

    rng = random.Random(_seed(train_number, class_code, date))
    demand = _train_demand(train_number) * CLASS_DEMAND_FACTOR.get(class_code, 1.0)
    demand = max(0.05, min(1.3, demand + rng.uniform(-0.08, 0.08)))

    base = base_fare(km) * CLASS_FACTOR[class_code]
    reservation = RESERVATION_CHARGE[class_code]
    superfast = SUPERFAST_CHARGE[class_code]
    gst = (base + reservation + superfast) * GST_RATE if class_code in GST_CLASSES else 0.0
    fare = round(base + reservation + superfast + gst)
    capacity = CLASS_CAPACITY.get(class_code, 72)

    if demand >= 1.05:
        return {"class": class_code, "fare": fare, "status": "REGRET", "kind": "regret"}
    if demand >= 0.6:
        quota = rng.choice(WAITLIST_QUOTAS)
        count = max(1, round((demand - 0.5) * capacity * rng.uniform(1.4, 3.2)))
        return {"class": class_code, "fare": fare, "status": f"{quota} {count}", "kind": "waitlist"}
    if demand >= 0.48:
        count = max(1, round((0.6 - demand) * capacity * rng.uniform(1.5, 3.0)))
        return {"class": class_code, "fare": fare, "status": f"RAC {count}", "kind": "rac"}
    count = max(1, round((0.48 - demand) * capacity * rng.uniform(2.0, 4.5)))
    return {"class": class_code, "fare": fare, "status": f"Available {count}", "kind": "available"}


def _seed(train_number: str, class_code: str, date: str) -> int:
    digest = hashlib.sha256(f"{train_number}:{class_code}:{date}".encode()).hexdigest()
    return int(digest[:16], 16)
