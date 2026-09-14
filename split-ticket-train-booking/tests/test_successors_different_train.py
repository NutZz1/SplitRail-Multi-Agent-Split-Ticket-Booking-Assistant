"""
Tests for src.successors_different_train against real data.

The connection used throughout is a real one from the timetables:

    12801 Purushottam Express   PURI -> NDLS, arrives NDLS 04:50 on journey day 3
    12217 Kerala Sampark Kranti KCVL -> CDG, departs NDLS 11:30 on ITS journey day 3

so on a date both run (2026-09-15, a Tuesday) a passenger arriving at NDLS
on 12801 has a real 400-minute buffer before 12217 leaves for UMB / CDG.
The buffer TIME is real (datameet timetables); the seat AVAILABILITY the
transfer also depends on is the package's synthetic layer.
"""

from __future__ import annotations

import datetime as dt

import pytest

from src.constraint_checks import coach_class, stop_datetime
from src.rail_graph import segment_minutes
from src.state import JourneyState, Ticket, UserQuery
from src.successors import MIN_COACH_SWITCH_MINUTES
from src.successors_different_train import (
    MIN_DIFFERENT_TRAIN_BUFFER_MINUTES,
    different_train_transfer_successors,
    get_successors_different_train,
)

PURUSHOTTAM, KERALA_SK, ISLAND = "12801", "12217", "16525"
TUESDAY = dt.date(2026, 9, 15)  # 12801 daily, 12217 Tue/Fri, 16525 Tue/Thu/Sat/Sun
QUERY = UserQuery("PURI", "CDG", TUESDAY, max_transfers=2)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def on_board(store, query: UserQuery, train: str, station: str, coach: str,
             *, tickets: tuple[Ticket, ...] = (), transfers: int = 0) -> JourneyState:
    """A real state: on ``train`` in ``coach``, just arrived at ``station`` (from the query origin)."""
    stops = store.get_real_halt_stops(train)
    idx = next(i for i, s in enumerate(stops) if s.station_code == station)
    origin_code = tickets[-1].to_station if tickets else query.origin_station
    origin_idx = next((i for i, s in enumerate(stops) if s.station_code == origin_code), 0)
    moving = sum(segment_minutes(a, b) for a, b in zip(stops[origin_idx:idx], stops[origin_idx + 1:idx + 1]))
    return JourneyState(
        current_station=station,
        arrival_datetime=stop_datetime(stops[idx], query.travel_date, use_arrival=idx > 0),
        current_train=train, current_coach=coach, current_class=coach_class(coach),
        tickets_so_far=tickets, transfer_count=transfers,
        passenger_count=query.passenger_count, cumulative_time_minutes=float(moving),
    )


def kinds(parent: JourneyState, children: list[JourneyState]) -> dict[str, list[JourneyState]]:
    out = {"CONTINUE": [], "DIFF_TRAIN": [], "SAME_TRAIN_COACH": []}
    for c in children:
        if c.current_train != parent.current_train:
            out["DIFF_TRAIN"].append(c)
        elif c.current_station != parent.current_station:
            out["CONTINUE"].append(c)
        else:
            out["SAME_TRAIN_COACH"].append(c)
    return out


def real_buffer_minutes(store, train_a: str, train_b: str, station: str, date: dt.date) -> float:
    arr = next(s for s in store.get_real_halt_stops(train_a) if s.station_code == station)
    dep = next(s for s in store.get_real_halt_stops(train_b) if s.station_code == station)
    return (stop_datetime(dep, date, use_arrival=False) - stop_datetime(arr, date, use_arrival=True)).total_seconds() / 60


# --------------------------------------------------------------------------- #
# the constant
# --------------------------------------------------------------------------- #
def test_buffer_is_stricter_than_same_train_switch():
    assert MIN_DIFFERENT_TRAIN_BUFFER_MINUTES >= 15
    assert MIN_DIFFERENT_TRAIN_BUFFER_MINUTES > MIN_COACH_SWITCH_MINUTES


# --------------------------------------------------------------------------- #
# the real connection: 12801 -> 12217 at NDLS
# --------------------------------------------------------------------------- #
def test_ndls_connection_is_grounded_in_real_timetable(store):
    buffer = real_buffer_minutes(store, PURUSHOTTAM, KERALA_SK, "NDLS", TUESDAY)
    arr = next(s for s in store.get_real_halt_stops(PURUSHOTTAM) if s.station_code == "NDLS")
    dep = next(s for s in store.get_real_halt_stops(KERALA_SK) if s.station_code == "NDLS")
    print(f"\nReal connection: {PURUSHOTTAM} arr NDLS {arr.arrival} day {arr.journey_day} -> "
          f"{KERALA_SK} dep NDLS {dep.departure} day {dep.journey_day}: buffer {buffer:.0f} min "
          f"(both anchored to travel_date {TUESDAY})")
    assert (arr.arrival, arr.journey_day) == ("04:50:00", 3)
    assert (dep.departure, dep.journey_day) == ("11:30:00", 3)
    assert buffer == 400
    assert buffer >= MIN_DIFFERENT_TRAIN_BUFFER_MINUTES
    assert store.runs_on_date(PURUSHOTTAM, TUESDAY.isoformat()) and store.runs_on_date(KERALA_SK, TUESDAY.isoformat())


def test_different_train_transfer_generated_at_ndls(store):
    state = on_board(store, QUERY, PURUSHOTTAM, "NDLS", "S1")
    assert state.arrival_datetime == dt.datetime(2026, 9, 17, 4, 50)
    succ = get_successors_different_train(state, QUERY, store)
    k = kinds(state, succ)
    print(f"\nAt NDLS on {PURUSHOTTAM} (its terminus), coach S1:")
    for c in succ:
        print(f"  -> board {c.current_train} in {c.current_coach}/{c.current_class} dep {c.arrival_datetime:%m-%d %H:%M}, "
              f"closes {c.tickets_so_far[-1].describe()}")
    assert k["CONTINUE"] == []  # NDLS is 12801's last stop
    assert k["SAME_TRAIN_COACH"] == []  # this generator never switches coach on the same train
    assert k["DIFF_TRAIN"], "expected a transfer onto 12217 at NDLS"
    for t in k["DIFF_TRAIN"]:
        assert t.current_train == KERALA_SK
        assert t.current_station == "NDLS"
        assert t.arrival_datetime == dt.datetime(2026, 9, 17, 11, 30)  # positioned at 12217's departure
        assert t.transfer_count == 1
        assert t.cumulative_time_minutes == state.cumulative_time_minutes  # layover is not moving time
        assert t.current_class == coach_class(t.current_coach)
        closed = t.tickets_so_far[-1]
        assert (closed.train_number, closed.from_station, closed.to_station, closed.coach) == (PURUSHOTTAM, "PURI", "NDLS", "S1")
        assert closed.boarding_datetime == dt.datetime(2026, 9, 15, 21, 50)
        assert closed.alighting_datetime == dt.datetime(2026, 9, 17, 4, 50)
        assert closed.status in {"CONFIRMED", "RAC", "WAITLIST"}
    # one successor per class of the connecting train
    assert len({t.current_class for t in k["DIFF_TRAIN"]}) == len(k["DIFF_TRAIN"])


def test_transfer_then_continue_reaches_cdg(store):
    """After transferring at NDLS, riding 12217 onward reaches the destination."""
    state = on_board(store, QUERY, PURUSHOTTAM, "NDLS", "S1")
    first = kinds(state, get_successors_different_train(state, QUERY, store))["DIFF_TRAIN"][0]
    nxt = kinds(first, get_successors_different_train(first, QUERY, store))["CONTINUE"]
    assert len(nxt) == 1 and nxt[0].current_station == "UMB"
    last = kinds(nxt[0], get_successors_different_train(nxt[0], QUERY, store))["CONTINUE"]
    assert len(last) == 1 and last[0].current_station == "CDG"
    assert last[0].arrival_datetime == dt.datetime(2026, 9, 17, 15, 45)


# --------------------------------------------------------------------------- #
# insufficient buffer
# --------------------------------------------------------------------------- #
def test_no_transfer_when_connecting_train_already_left(store):
    """Real negative gap: 16525 reaches TCR 19:40 but 12217 left TCR at 14:20 the same day."""
    q = UserQuery("CAPE", "CDG", TUESDAY, max_transfers=2)
    buffer = real_buffer_minutes(store, ISLAND, KERALA_SK, "TCR", TUESDAY)
    print(f"\nReal non-connection: {ISLAND} -> {KERALA_SK} at TCR: buffer {buffer:.0f} min")
    assert buffer < 0
    state = on_board(store, q, ISLAND, "TCR", "S1")
    assert [c for c in different_train_transfer_successors(state, q, store) if c.current_train == KERALA_SK] == []


def test_no_transfer_when_buffer_below_threshold(store):
    """Boundary on the real 400-minute NDLS gap: 400 passes, 401 is pruned."""
    state = on_board(store, QUERY, PURUSHOTTAM, "NDLS", "S1")
    ok = different_train_transfer_successors(state, QUERY, store, min_buffer_minutes=400)
    assert ok and all(c.current_train == KERALA_SK for c in ok)
    assert different_train_transfer_successors(state, QUERY, store, min_buffer_minutes=401) == []


# --------------------------------------------------------------------------- #
# hard constraints
# --------------------------------------------------------------------------- #
def test_run_date_prunes_connecting_train(store):
    # 12217 does not run on Wednesday 16th; 12801 (daily) still does.
    wed = UserQuery("PURI", "CDG", dt.date(2026, 9, 16), max_transfers=2)
    assert not store.runs_on_date(KERALA_SK, "2026-09-16")
    state = on_board(store, wed, PURUSHOTTAM, "NDLS", "S1")
    assert get_successors_different_train(state, wed, store) == []


def test_max_transfers_prunes(store):
    q0 = UserQuery("PURI", "CDG", TUESDAY, max_transfers=0)
    state = on_board(store, q0, PURUSHOTTAM, "NDLS", "S1")
    assert get_successors_different_train(state, q0, store) == []
    # at the limit already
    q1 = UserQuery("PURI", "CDG", TUESDAY, max_transfers=1)
    prior = Ticket(PURUSHOTTAM, "PURI", "GAYA", "S1", "SL", dt.datetime(2026, 9, 15, 21, 50), dt.datetime(2026, 9, 16, 22, 0), "CONFIRMED")
    state = on_board(store, q1, PURUSHOTTAM, "NDLS", "S1", tickets=(prior,), transfers=1)
    assert [c for c in get_successors_different_train(state, q1, store) if c.transfer_count > 1] == []


def test_class_hard_constraint_prunes(store):
    # 12217 has no CC; a hard CC constraint yields nothing at NDLS
    q_cc = UserQuery("PURI", "CDG", TUESDAY, travel_class_preference="CC", class_is_hard_constraint=True)
    state = on_board(store, q_cc, PURUSHOTTAM, "NDLS", "S1")
    assert get_successors_different_train(state, q_cc, store) == []  # closing an SL ticket also violates CC
    # hard SL: transfers exist and are all SL
    q_sl = UserQuery("PURI", "CDG", TUESDAY, travel_class_preference="SL", class_is_hard_constraint=True)
    state = on_board(store, q_sl, PURUSHOTTAM, "NDLS", "S1")
    succ = get_successors_different_train(state, q_sl, store)
    assert succ and all(c.current_class == "SL" and c.current_train == KERALA_SK for c in succ)


def test_connecting_train_must_reach_destination(store):
    # From NDLS, 12217 reaches CDG. For a destination it never reaches (e.g. MAS) no transfer is offered.
    q = UserQuery("PURI", "MAS", TUESDAY, max_transfers=2)
    state = on_board(store, q, PURUSHOTTAM, "NDLS", "S1")
    assert different_train_transfer_successors(state, q, store) == []


def test_no_transfer_at_boarding_station(store):
    # Just boarded 12801 at PURI: switching trains here is a board choice, not a transfer.
    state = on_board(store, QUERY, PURUSHOTTAM, "PURI", "S1")
    assert different_train_transfer_successors(state, QUERY, store) == []


def test_cycle_prevention(store):
    # A closed ticket already ended at UMB (12217's next halt after NDLS): boarding 12217 at NDLS is refused.
    fake = Ticket("99999", "PURI", "UMB", "S1", "SL", dt.datetime(2026, 9, 15), dt.datetime(2026, 9, 16), "CONFIRMED")
    state = on_board(store, QUERY, PURUSHOTTAM, "NDLS", "S1", tickets=(fake,), transfers=1)
    assert [c for c in different_train_transfer_successors(state, QUERY, store) if c.current_train == KERALA_SK] == []


# --------------------------------------------------------------------------- #
# board / continue are the same-train ones
# --------------------------------------------------------------------------- #
def test_board_and_continue_match_same_train_generator(store, mail_query):
    from src.state import initial_state
    from src.successors import get_successors
    start = initial_state(mail_query)
    assert get_successors_different_train(start, mail_query, store) == get_successors(start, mail_query, store)
    boarded = get_successors(start, mail_query, store)[0]
    same = kinds(boarded, get_successors(boarded, mail_query, store))["CONTINUE"]
    diff = kinds(boarded, get_successors_different_train(boarded, mail_query, store))["CONTINUE"]
    assert same == diff
