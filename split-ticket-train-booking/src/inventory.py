"""Authoritative, deterministic physical-seat inventory for the simulation.

Seats 1..capacity and berth types are derived from the local coach/layout
records. A versioned SHA256 seed produces *occupied intervals*, never
independent origin/destination statuses. Thus two available adjacent pieces
of the SAME seat always compose into an available through journey.

Initial occupancy is a reproducible computed seed layer, not real ridership.
The busy-v2 profile leaves scarce through seats, fragmented seats changing
at real long halts, or fully occupied coaches. Existing booked runs retain
their standard-v1 seed. No route or station code is special-cased.
Reservations overlay this same layer, keyed by the train's origin run date.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from src.config import INVENTORY_SEED, MIN_SEAT_CHANGE
from src import config


def stable(*parts) -> int:
    return int.from_bytes(hashlib.sha256(':'.join(map(str, (INVENTORY_SEED, *parts))).encode()).digest()[:8], 'big')


@dataclass(frozen=True)
class Seat:
    train_number: str
    coach: str
    number: int
    travel_class: str
    berth_type: str | None
    position: int

    @property
    def id(self):
        return f'{self.train_number}:{self.coach}:{self.number}'


@lru_cache(maxsize=1)
def layouts():
    return json.loads((Path(__file__).resolve().parent.parent / 'data_source/berth_layout_rules.json').read_text())


class InventoryService:
    """One short-lived read snapshot per search, or a transaction's connection.

    A new instance observes all committed bookings; per-instance caches avoid
    repeating SQL for each A* successor. Do not share instances across writes.
    """
    def __init__(self, store, bookings=None):
        self.store = store
        self.bookings = bookings if bookings is not None else store._bookings()
        self._routes = {}
        self._coaches = {}
        self._occupied = {}
        self._free = {}
        self._seats = {}
        self._runs = {}
        self._indices = {}
        self._initial = {}
        self._profiles = {}

    def profile(self, train, run_date):
        key = train, str(run_date)
        if key not in self._profiles:
            self._profiles[key] = (self.bookings.inventory_profile(*key) if self.bookings is not None
                                   else config.DEFAULT_INVENTORY_PROFILE)
        return self._profiles[key]

    def _busy_masks(self, train, run_date, coach):
        """Scarce free intervals separated at real, sufficiently long halts.

        30% of train/run/classes have a few through seats, 60% have fragmented
        seat windows, and 10% are fully occupied. All coaches of a class share
        boundaries, so crossing one requires a real seat change. No station
        pair is special-cased; no random work happens at request time.
        """
        route = self.route(train)
        n = len(route) - 1
        all_segments = (1 << n) - 1
        cls = self.store.coach_travel_class(coach)
        seats = self.seats(train, coach)
        masks = {s.number: all_segments for s in seats}
        if not seats or n < 1:
            return masks
        tier = stable(train, run_date, cls, 'demand-v2') % 10
        if tier == 9:
            return masks
        ordered = sorted(masks, key=lambda number: stable(train, run_date, coach, number, 'seat-order-v2'))
        quota = 1 + stable(train, run_date, coach, 'quota-v2') % max(1, min(4, len(seats) // 16))
        if tier < 3:
            for number in ordered[:quota]:
                masks[number] = 0
            return masks
        boundaries = [0]
        spacing = 7 + stable(train, run_date, cls, 'spacing-v2') % 5
        for i, stop in enumerate(route[1:-1], 1):
            if i - boundaries[-1] >= spacing and (stop.halt_minutes or 0) >= MIN_SEAT_CHANGE:
                boundaries.append(i)
        boundaries.append(n)
        for block, (a, b) in enumerate(zip(boundaries, boundaries[1:])):
            free_span = ((1 << b) - 1) ^ ((1 << a) - 1)
            for offset in range(quota):
                number = ordered[(block * quota + offset) % len(ordered)]
                masks[number] &= ~free_span
        return masks

    def route(self, train):
        if train not in self._routes:
            self._routes[train] = self.store.get_real_halt_stops(train)
        return self._routes[train]

    def coaches(self, train):
        if train not in self._coaches:
            self._coaches[train] = [c for c in dict.fromkeys(self.store.get_coach_composition(train))
                                    if self.store.coach_travel_class(c) and self.store.get_berth_rule(self.store.coach_travel_class(c))]
        return self._coaches[train]

    def seats(self, train, coach):
        if (train, coach) in self._seats:
            return self._seats[train, coach]
        if coach not in self.coaches(train):
            return ()
        cls = self.store.coach_travel_class(coach)
        rule = layouts().get(cls, {})
        kinds = rule.get('berth_types_per_bay') or []
        capacity = rule.get('berths_per_coach') or 0
        result = tuple(Seat(train, coach, n, cls, kinds[(n - 1) % len(kinds)] if kinds else None, n)
                       for n in range(1, capacity + 1))
        self._seats[train, coach] = result
        return result

    def span(self, train, origin, destination):
        if train not in self._indices:
            self._indices[train] = {s.station_code: i for i, s in enumerate(self.route(train))}
        indices = self._indices[train]
        a, b = indices.get(origin), indices.get(destination)
        if a is None or b is None or a >= b:
            raise ValueError(f'Train {train} has no forward stopping route {origin} → {destination}.')
        return a, b

    def operates(self, train, run_date):
        key = train, str(run_date)
        if key not in self._runs:
            date = dt.date.fromisoformat(str(run_date))
            self._runs[key] = date.strftime('%a').upper() in self.store.get_run_pattern(train)
        return self._runs[key]

    def initial_masks(self, train, run_date, coach):
        key = train, str(run_date), coach
        if key in self._initial:
            return dict(self._initial[key])
        if self.profile(train, run_date) == 'busy-v2':
            masks = self._busy_masks(train, run_date, coach)
            self._initial[key] = dict(masks)
            return masks
        stops = self.route(train)
        n = len(stops) - 1
        if n < 1:
            return {}
        all_segments = (1 << n) - 1
        cls = self.store.coach_travel_class(coach)
        busy = stable(train, run_date, cls, 'busy') % 5 == 0
        pivots = [i for i, s in enumerate(stops[1:-1], 1) if (s.halt_minutes or 0) >= MIN_SEAT_CHANGE]
        pivot = pivots[stable(train, run_date, cls, 'pivot') % len(pivots)] if pivots else max(1, n // 2)
        prefix = (1 << pivot) - 1
        masks = {}
        for seat in self.seats(train, coach):
            h = stable(train, run_date, coach, seat.number)
            if busy:
                mask = prefix if h % 2 else all_segments ^ prefix
            elif h % 10 < 2:
                mask = 0  # two tenths initially free throughout
            elif h % 10 < 6:
                mask = all_segments
            else:
                a = (h // 10) % n
                b = a + 1 + (h // 100) % (n - a)
                mask = ((1 << b) - 1) ^ ((1 << a) - 1)
            masks[seat.number] = mask
        self._initial[key] = dict(masks)
        return masks

    def occupied(self, train, run_date, coach):
        key = (train, str(run_date), coach)
        if key in self._occupied:
            return self._occupied[key]
        masks = self.initial_masks(*key)
        if self.bookings is not None:
            held = self.bookings.active_for_run(train, str(run_date))
            for booking in held:
                if booking.coach_code != coach:
                    continue
                a, b = self.span(train, booking.from_station, booking.to_station)
                span = ((1 << b) - 1) ^ ((1 << a) - 1)
                numbers = booking.seat_numbers
                if not numbers:
                    # Preserve pre-migration reservations conservatively. They
                    # had no seat identities; consume remaining seats in order.
                    numbers = tuple(n for n, mask in masks.items() if not mask & span)[:booking.passenger_count]
                for number in numbers:
                    if number in masks:
                        masks[number] |= span
        self._occupied[key] = masks
        return masks

    def available(self, train, run_date, origin, destination, coach):
        key = (train, str(run_date), origin, destination, coach)
        if key not in self._free:
            a, b = self.span(train, origin, destination)
            span = ((1 << b) - 1) ^ ((1 << a) - 1)
            self._free[key] = tuple(n for n, mask in self.occupied(train, str(run_date), coach).items() if not mask & span)
        return self._free[key]

    def snapshot(self, train, run_date, origin, destination, coach, passengers=1):
        free = self.available(train, run_date, origin, destination, coach)
        if len(free) >= passengers:
            status = 'CONFIRMED'
        else:
            # Risk labels indicate an unallocated request, NEVER a physical
            # seat promise. Only confirmed seats can be reserved by this demo.
            status = ('RAC', 'WAITLIST', 'UNAVAILABLE')[stable(train, run_date, coach, origin, destination, 'queue') % 3]
            a, b = self.span(train, origin, destination)
            span = ((1 << b) - 1) ^ ((1 << a) - 1)
            if not free and any(not mask & span for mask in self.initial_masks(train, str(run_date), coach).values()):
                status = 'UNAVAILABLE'
        return {'status': status, 'seat_numbers': free[:passengers] if status == 'CONFIRMED' else (),
                'remaining': len(free), 'capacity': len(self.seats(train, coach))}
