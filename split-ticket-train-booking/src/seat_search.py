"""Date-aware ticket-edge successor graph shared by A*, BFS and UCS.

An edge rides one validated seat allocation between two real ordered halts.
Compressing continue actions into a whole ticket avoids branching at every
pass-through. g is still the sum of consecutive-halt moving times, so the
existing relaxed-graph Dijkstra heuristic remains a lower bound.
"""
from __future__ import annotations

import datetime as dt
import logging
from collections import Counter
from dataclasses import replace

from src.config import MIN_SEAT_CHANGE, MIN_TRAIN_CHANGE, MAX_SHORT_WALK, MAX_JOURNEY_DAYS, SEAT_CANDIDATES
from src.constraint_checks import stop_datetime
from src.inventory import InventoryService
from src.rail_graph import segment_minutes
from src.state import Ticket

log = logging.getLogger(__name__)


def operates_on_boarding_date(store, train, station, boarding_date):
    """Use the same date semantics for failure explanations as for search."""
    stop = next((s for s in store.get_real_halt_stops(train) if s.station_code == station), None)
    if stop is None:
        return False
    run = boarding_date - dt.timedelta(days=(stop.journey_day or 1) - 1)
    return run.strftime('%a').upper() in store.get_run_pattern(train)


def search_limit_reason(query, store, stats):
    window = store.get_window()
    if window and not window[0] <= query.travel_date.isoformat() <= window[1]:
        return f"Boarding date {query.travel_date} is outside the booking window ({window[0]} .. {window[1]})."
    if stats.truncated:
        return "Search limit reached before finding a journey; a valid option may still exist. Try fewer transfers."
    if query.region:
        covered = store.get_operational_station_codes()
        missing = [c for c in (query.origin_station, query.destination_station) if c not in covered]
        if missing:
            return f"Halt timetable data unavailable for {', '.join(missing)} in the local Kerala dataset. No journey can be verified."
    return None


class SeatSuccessors:
    def __init__(self, store, query, mode='same_train'):
        self.store, self.query, self.mode = store, query, mode
        self.inventory = InventoryService(store)
        self.seat_checks = 0
        self.rejections = Counter()
        self.routes = {t: self.inventory.route(t) for t in store.get_demo_train_numbers()}
        self.region_codes = {c for c, _ in store.get_region_stations(query.region)} if query.region else None
        self.index = {}
        for train, stops in self.routes.items():
            for i, stop in enumerate(stops[:-1]):
                if self.region_codes is not None and stop.station_code not in self.region_codes:
                    continue
                self.index.setdefault(stop.station_code, []).append((train, i))
        self.destination = query.destination_station
        self.ends = {t: next((i for i, s in enumerate(stops) if s.station_code == self.destination), None)
                     for t, stops in self.routes.items()}

    def __call__(self, state, query, store):
        if state.tickets_so_far and state.transfer_count >= query.max_transfers:
            return []
        result = []
        previous = state.tickets_so_far[-1] if state.tickets_so_far else None
        for train, i in self.index.get(state.current_station, []):
            same = previous is not None and train == previous.train_number
            if previous and ((self.mode == 'same_train' and not same) or (self.mode == 'different_train' and same)):
                continue
            if self.mode == 'same_train' and (self.ends[train] is None or self.ends[train] <= i):
                continue
            stops = self.routes[train]
            here = stops[i]
            if same and (query.seat_change_tolerance == 'none' or (here.halt_minutes or 0) < MIN_SEAT_CHANGE):
                continue
            if same:
                dates = [dt.date.fromisoformat(previous.train_run_date)]
            else:
                # Query date is the passenger's BOARDING date, not train-origin
                # date. Connections can board the following calendar day.
                first = state.arrival_datetime.date() if previous else query.travel_date
                dates = [first + dt.timedelta(days=d - ((here.journey_day or 1) - 1))
                         for d in range(2 if previous else 1)]
            for run in dates:
                if not self.inventory.operates(train, run):
                    self.rejections['not_operating'] += 1
                    continue
                departure = stop_datetime(here, run, use_arrival=False)
                if departure is None:
                    continue
                if previous and (departure - state.arrival_datetime).total_seconds()/60 < (MIN_SEAT_CHANGE if same else MIN_TRAIN_CHANGE):
                    self.rejections['insufficient_transfer_time'] += 1
                    continue
                if same and run.isoformat() != previous.train_run_date:
                    continue
                moving = 0.0
                for j in range(i + 1, len(stops)):
                    minutes = segment_minutes(stops[j-1], stops[j])
                    if minutes is None or minutes <= 0:
                        self.rejections['inconsistent_timetable'] += 1
                        break  # never cross an inconsistent timetable edge
                    moving += minutes
                    there = stops[j]
                    if self.region_codes is not None and there.station_code not in self.region_codes:
                        continue
                    goal = there.station_code == self.destination
                    if there.station_code in state.visited_stations:
                        continue
                    if not goal:
                        if len(state.tickets_so_far) >= query.max_transfers:
                            continue
                        if self.mode == 'same_train':
                            if query.seat_change_tolerance == 'none' or (there.halt_minutes or 0) < MIN_SEAT_CHANGE:
                                continue
                        elif not any(t != train and self.ends[t] is not None and self.ends[t] > idx
                                     for t, idx in self.index.get(there.station_code, [])):
                            continue
                    arrival = stop_datetime(there, run, use_arrival=True)
                    if arrival is None or arrival <= departure or arrival > dt.datetime.combine(query.travel_date + dt.timedelta(days=MAX_JOURNEY_DAYS), dt.time.max):
                        continue
                    choices = {}
                    for coach in self.inventory.coaches(train):
                        cls = store.coach_travel_class(coach)
                        if query.class_is_hard_constraint and cls != query.travel_class_preference:
                            continue
                        distance = store.coach_distance(train, previous.coach, coach) if same else 0
                        if same and query.seat_change_tolerance == 'short_walk' and (distance is None or distance > MAX_SHORT_WALK):
                            continue
                        self.seat_checks += 1
                        snap = self.inventory.snapshot(train, run.isoformat(), here.station_code, there.station_code, coach, query.passenger_count)
                        if previous or not goal:
                            if snap['status'] != 'CONFIRMED':
                                self.rejections['split_requires_confirmed_seats'] += 1
                                continue  # never promise an unallocated split leg
                        numbers = snap['seat_numbers']
                        if same and previous.coach == coach and previous.seat_numbers == numbers:
                            free = self.inventory.available(train, run.isoformat(), here.station_code, there.station_code, coach)
                            numbers = tuple(n for n in free if n not in previous.seat_numbers)[:query.passenger_count]
                            if len(numbers) < query.passenger_count:
                                continue
                        if snap['status'] == 'UNAVAILABLE':
                            self.rejections['unavailable'] += 1
                            continue
                        priority = ({'CONFIRMED': 0, 'RAC': 1, 'WAITLIST': 2}[snap['status']], distance or 0, coach, numbers)
                        if cls not in choices or priority < choices[cls][0]:
                            choices[cls] = (priority, coach, numbers, snap['status'])
                    for cls, (_, coach, numbers, status) in sorted(choices.items()):
                        ticket = Ticket(train, here.station_code, there.station_code, coach, cls,
                                        departure, arrival, status, numbers, run.isoformat())
                        result.append(replace(state, current_station=there.station_code, arrival_datetime=arrival,
                                              current_train=train, current_coach=coach, current_class=cls,
                                              tickets_so_far=state.tickets_so_far + (ticket,), tickets_closed=True,
                                              transfer_count=len(state.tickets_so_far),
                                              cumulative_time_minutes=state.cumulative_time_minutes + moving))
                    if goal:
                        break
        return result


def search_seats(query, store, heuristic, mode, k=SEAT_CANDIDATES):
    from src.search_astar import astar_search_k
    from src.state import initial_state
    from src.search_stats import SearchStats
    window = store.get_window()
    if window and not window[0] <= query.travel_date.isoformat() <= window[1]:
        return [], SearchStats(algorithm='A*')
    successor = SeatSuccessors(store, query, mode)
    goals, stats = astar_search_k(query, store, heuristic, k=k, successors_fn=successor,
                                 signature_fn=lambda g: tuple((t.train_number, t.train_run_date, t.from_station,
                                                              t.to_station, t.travel_class, t.status) for t in g.tickets_so_far))
    # The bounded split shortlist must not hide direct operational services.
    # These are exactly the graph's one-edge goals, validated by the same code.
    if mode == 'same_train':
        direct = [s for s in successor(initial_state(query), query, store) if s.current_station == query.destination_station]
        keys = {tuple(s.tickets_so_far) for s in goals}
        goals.extend(s for s in direct if tuple(s.tickets_so_far) not in keys)
    else:
        goals = [s for s in goals if len(s.tickets_so_far) > 1]
    stats.seat_checks = successor.seat_checks
    stats.rejections = dict(successor.rejections)
    log.info('search_agent mode=%s expanded=%d candidates=%d seconds=%.3f truncated=%s seat_checks=%d rejections=%s',
             mode, stats.nodes_expanded, len(goals), stats.wall_clock_seconds, stats.truncated,
             stats.seat_checks, stats.rejections)
    return goals, stats
