"""
Tests for src.state and src.successors against real railway.db data.

Train 12658 (Bangalore-Chennai Mail, SBC -> MAS) is the workhorse: its real
halts are SBC, BNC (5 min), BWT, JTJ, KPD, AJJ, PER (all 2 min), MAS.
Only BNC's halt reaches MIN_COACH_SWITCH_MINUTES, so it is the one place a
same-train coach transfer should be generated.
"""

from __future__ import annotations

import datetime as dt

import pytest

from src.data_store import RailDataStore
from src.rail_graph import segment_minutes
from src.state import JourneyState, Ticket, UserQuery, initial_state
from src.constraint_checks import bookable_coaches
from src.successors import (
    MIN_COACH_SWITCH_MINUTES,
    best_coach_per_class,
    coach_class,
    get_successors,
    stop_datetime,
)

MAIL = "12658"
DATE = dt.date(2026, 9, 16)  # a Wednesday; 12658 runs daily
QUERY = UserQuery(origin_station="SBC", destination_station="MAS", travel_date=DATE, max_transfers=2)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def on_board(store: RailDataStore, query: UserQuery, station: str, coach: str,
             *, tickets: tuple[Ticket, ...] = (), transfers: int = 0) -> JourneyState:
    """A real state: on 12658 in ``coach``, just arrived at ``station`` (or departing, at SBC)."""
    stops = store.get_real_halt_stops(MAIL)
    idx = next(i for i, s in enumerate(stops) if s.station_code == station)
    moving = sum(segment_minutes(a, b) for a, b in zip(stops[:idx], stops[1:idx + 1]))
    when = stop_datetime(stops[idx], query.travel_date, use_arrival=idx > 0)
    return JourneyState(
        current_station=station,
        arrival_datetime=when,
        current_train=MAIL,
        current_coach=coach,
        current_class=coach_class(coach),
        tickets_so_far=tickets,
        transfer_count=transfers,
        passenger_count=query.passenger_count,
        cumulative_time_minutes=float(moving),
    )


def describe_move(parent: JourneyState, child: JourneyState) -> str:
    if not parent.is_boarded:
        return f"BOARD    {child.current_train} at {child.current_station} in {child.current_coach}/{child.current_class} dep {child.arrival_datetime:%H:%M}"
    if child.current_station != parent.current_station:
        return (f"CONTINUE {parent.current_station}->{child.current_station} in {child.current_coach} "
                f"arr {child.arrival_datetime:%m-%d %H:%M} (+{child.cumulative_time_minutes - parent.cumulative_time_minutes:.0f} min, g={child.cumulative_time_minutes:.0f})")
    closed = child.tickets_so_far[-1].describe()
    return f"TRANSFER at {child.current_station}: {parent.current_coach} -> {child.current_coach}/{child.current_class}, closes ticket {closed}"


def kinds(parent: JourneyState, children: list[JourneyState]) -> dict[str, list[JourneyState]]:
    out: dict[str, list[JourneyState]] = {"BOARD": [], "CONTINUE": [], "TRANSFER": []}
    for c in children:
        out[describe_move(parent, c).split()[0]].append(c)
    return out


def print_successors(title: str, parent: JourneyState, children: list[JourneyState]) -> None:
    print(f"\n=== {title} ===\nparent: {parent.describe()}")
    if not children:
        print("  (no successors)")
    for c in children:
        print("  ->", describe_move(parent, c))


# --------------------------------------------------------------------------- #
# state.py
# --------------------------------------------------------------------------- #
def test_journey_state_is_hashable_and_comparable():
    a = initial_state(QUERY)
    b = initial_state(QUERY)
    assert a == b and hash(a) == hash(b)
    assert len({a, b}) == 1


def test_tickets_list_is_coerced_to_tuple():
    s = JourneyState("SBC", dt.datetime(2026, 9, 16), None, None, None, [], 0, 1, 0.0)  # type: ignore[arg-type]
    assert isinstance(s.tickets_so_far, tuple)
    hash(s)


def test_user_query_validation():
    with pytest.raises(TypeError):
        UserQuery("SBC", "MAS", dt.datetime(2026, 9, 16))  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        UserQuery("SBC", "MAS", DATE, class_is_hard_constraint=True)
    with pytest.raises(ValueError):
        UserQuery("SBC", "MAS", DATE, max_transfers=-1)


def test_initial_state_shape():
    s = initial_state(QUERY)
    assert s.current_station == "SBC" and not s.is_boarded
    assert s.arrival_datetime == dt.datetime(2026, 9, 16, 0, 0)
    assert s.tickets_so_far == () and s.transfer_count == 0 and s.cumulative_time_minutes == 0.0


def test_ticket_risk_flag():
    t = Ticket(MAIL, "SBC", "BNC", "S1", "SL", dt.datetime(2026, 9, 16, 22, 45), dt.datetime(2026, 9, 16, 22, 55), "RAC")
    assert t.is_risky and t.duration_minutes == 10
    assert not Ticket(MAIL, "SBC", "BNC", "S1", "SL", t.boarding_datetime, t.alighting_datetime, "CONFIRMED").is_risky


@pytest.mark.parametrize("code,cls", [
    ("S1", "SL"), ("S13", "SL"), ("B1", "3A"), ("BE1", "3A"), ("A1", "2A"), ("H1", "1A"),
    ("D12", "CC"), ("GS", None), ("GEN", None), ("EOG", None), ("PC", None), ("SLR", None), (None, None),
])
def test_coach_class(code, cls):
    assert coach_class(code) == cls


def test_stop_datetime_crosses_midnight(store):
    stops = store.get_real_halt_stops(MAIL)
    sbc, mas = stops[0], stops[-1]
    assert stop_datetime(sbc, DATE, use_arrival=False) == dt.datetime(2026, 9, 16, 22, 45)
    assert stop_datetime(mas, DATE, use_arrival=True) == dt.datetime(2026, 9, 17, 4, 40)
    assert stop_datetime(sbc, DATE, use_arrival=True) is None  # origin has no arrival


def test_best_coach_per_class_prefers_status_then_proximity():
    comp = ["EOG", "S1", "S2", "S3", "B1", "B2", "A1", "EOG"]
    avail = {"S1": "WAITLIST", "S2": "CONFIRMED", "S3": "CONFIRMED", "B1": "UNAVAILABLE", "B2": "RAC", "A1": "CONFIRMED"}
    picks = best_coach_per_class(avail, comp, exclude="S3", near="S3")
    assert picks == [("S2", "SL", "CONFIRMED"), ("B2", "3A", "RAC"), ("A1", "2A", "CONFIRMED")]
    # without exclusion S3 wins SL because it is distance 0 from itself
    assert best_coach_per_class(avail, comp, near="S3")[0] == ("S3", "SL", "CONFIRMED")


# --------------------------------------------------------------------------- #
# boarding at SBC
# --------------------------------------------------------------------------- #
def test_boarding_at_sbc_includes_12658(store):
    start = initial_state(QUERY)
    succ = get_successors(start, QUERY, store)
    print_successors("Boarding at SBC on 2026-09-16", start, succ)
    trains = {s.current_train for s in succ}
    assert MAIL in trains
    for s in succ:
        assert s.is_boarded and s.current_station == "SBC"
        assert s.current_coach is not None and s.current_class == coach_class(s.current_coach)
        assert s.tickets_so_far == () and s.cumulative_time_minutes == 0.0
        assert store.runs_on_date(s.current_train, DATE.isoformat())
    mail_boards = [s for s in succ if s.current_train == MAIL]
    assert mail_boards[0].arrival_datetime == dt.datetime(2026, 9, 16, 22, 45)
    # one successor per class with a bookable coach; 12658 has SL/3A/2A/1A coaches
    assert {s.current_class for s in mail_boards} == {"SL", "3A", "2A", "1A"}


def test_boarding_excludes_train_that_terminates_here(store):
    # 12609 runs on the 16th and halts at SBC, but SBC is its last stop
    assert store.runs_on_date("12609", DATE.isoformat())
    succ = get_successors(initial_state(QUERY), QUERY, store)
    assert "12609" not in {s.current_train for s in succ}


def test_boarding_respects_run_date(store):
    q = UserQuery("SBC", "MAS", dt.date(2026, 9, 26))  # outside the run-date window
    assert get_successors(initial_state(q), q, store) == []


# --------------------------------------------------------------------------- #
# BNC (5-min halt) vs KPD (2-min halt)
# --------------------------------------------------------------------------- #
def test_at_bnc_continue_and_transfer_are_generated(store):
    state = on_board(store, QUERY, "BNC", "S1")
    succ = get_successors(state, QUERY, store)
    print_successors(f"On board 12658 at BNC (halt 5 min, coach S1), MIN_COACH_SWITCH_MINUTES={MIN_COACH_SWITCH_MINUTES}", state, succ)
    k = kinds(state, succ)

    # a) continue to the next real halt (BWT) in the same coach
    assert len(k["CONTINUE"]) == 1
    cont = k["CONTINUE"][0]
    assert cont.current_station == "BWT" and cont.current_coach == "S1"
    assert cont.arrival_datetime == dt.datetime(2026, 9, 16, 23, 48)
    assert cont.cumulative_time_minutes == state.cumulative_time_minutes + 48
    assert cont.transfer_count == 0 and cont.tickets_so_far == ()

    # b) same-train transfer to a different coach with room on BNC->BWT
    # An alternate coach qualifies if a ticket BNC -> X is bookable in it for
    # some halt X ahead (a ticket needs one bookable pair: its own endpoints).
    ahead = ["BWT", "JTJ", "KPD", "AJJ", "PER", "MAS"]
    alternates = bookable_coaches(store, MAIL, "BNC", ahead)
    alternates.pop("S1", None)
    if not alternates:
        pytest.skip("no alternate coach with a bookable ticket from BNC in this dataset")
    assert k["TRANSFER"], "expected a same-train transfer at the 5-minute BNC halt"
    for t in k["TRANSFER"]:
        assert t.current_station == "BNC" and t.current_train == MAIL
        assert t.current_coach != "S1" and t.current_coach in alternates
        assert t.transfer_count == 1
        assert len(t.tickets_so_far) == 1
        closed = t.tickets_so_far[0]
        assert (closed.train_number, closed.from_station, closed.to_station, closed.coach) == (MAIL, "SBC", "BNC", "S1")
        assert closed.boarding_datetime == dt.datetime(2026, 9, 16, 22, 45)
        assert closed.alighting_datetime == dt.datetime(2026, 9, 16, 22, 55)
        assert closed.status == store.get_availability(MAIL, "SBC", "BNC")["S1"]
        assert t.cumulative_time_minutes == state.cumulative_time_minutes  # transferring costs no moving time
    # one transfer per class, and the SL pick is the nearest bookable S-coach to S1
    assert len({t.current_class for t in k["TRANSFER"]}) == len(k["TRANSFER"])


def test_at_kpd_no_transfer_is_generated(store):
    state = on_board(store, QUERY, "KPD", "S1")
    succ = get_successors(state, QUERY, store)
    print_successors("On board 12658 at KPD (halt 2 min, coach S1)", state, succ)
    k = kinds(state, succ)
    assert len(k["CONTINUE"]) == 1 and k["CONTINUE"][0].current_station == "AJJ"
    # alternates DO exist at KPD -- so it must be the halt-time rule doing the pruning
    onward = store.get_availability(MAIL, "KPD", "AJJ")
    assert any(c != "S1" and st != "UNAVAILABLE" for c, st in onward.items())
    assert k["TRANSFER"] == []


@pytest.mark.parametrize("station", ["BWT", "JTJ", "AJJ", "PER"])
def test_two_minute_halts_never_transfer(store, station):
    state = on_board(store, QUERY, station, "S1")
    assert kinds(state, get_successors(state, QUERY, store))["TRANSFER"] == []


def test_no_transfer_at_boarding_station(store):
    # SBC is the ticket origin: nothing to close, the board move already chose the coach
    state = on_board(store, QUERY, "SBC", "S1")
    k = kinds(state, get_successors(state, QUERY, store))
    assert k["TRANSFER"] == [] and len(k["CONTINUE"]) == 1


def test_which_constraint_prunes_transfers_on_12658(store):
    """Diagnostic for the report: at each intermediate halt, is it halt time or availability that blocks?"""
    stops = store.get_real_halt_stops(MAIL)
    print(f"\n{'halt':<5}{'halt_min':>9}{'alt coaches w/ room':>21}{'blocked by':>28}")
    for here, nxt in zip(stops[1:-1], stops[2:-1] + [stops[-1]]):
        onward = store.get_availability(MAIL, here.station_code, nxt.station_code)
        alternates = [c for c, st in onward.items() if c != "S1" and st != "UNAVAILABLE"]
        state = on_board(store, QUERY, here.station_code, "S1")
        transfers = kinds(state, get_successors(state, QUERY, store))["TRANSFER"]
        if transfers:
            blocked = "-- (transfer generated)"
        elif here.halt_minutes < MIN_COACH_SWITCH_MINUTES and alternates:
            blocked = "halt time only"
        elif here.halt_minutes >= MIN_COACH_SWITCH_MINUTES and not alternates:
            blocked = "availability only"
        else:
            blocked = "both"
        print(f"{here.station_code:<5}{here.halt_minutes:>9.0f}{len(alternates):>21}{blocked:>28}")
        assert bool(transfers) == (here.halt_minutes >= MIN_COACH_SWITCH_MINUTES and bool(alternates))


# --------------------------------------------------------------------------- #
# riding to the goal / availability pruning
# --------------------------------------------------------------------------- #
def test_arrival_at_mas_has_no_successors_and_correct_time(store):
    state = on_board(store, QUERY, "MAS", "S1")
    assert state.arrival_datetime == dt.datetime(2026, 9, 17, 4, 40)
    assert state.cumulative_time_minutes == 340
    assert get_successors(state, QUERY, store) == []


def _first_unbookable_ahead(store):
    """(station, coach) where NO ticket SBC -> X is bookable in `coach` for any halt X ahead of `station`."""
    stops = store.get_real_halt_stops(MAIL)
    for i, here in enumerate(stops[1:-1], start=1):
        ahead = [s.station_code for s in stops[i + 1:]]
        options = bookable_coaches(store, MAIL, "SBC", ahead)
        for coach in store.get_availability(MAIL, "SBC", "BNC"):
            if coach not in options:
                return here.station_code, coach
    return None


def test_continue_is_pruned_when_ticket_can_no_longer_be_closed(store):
    """A coach whose in-progress ticket (from SBC) has no bookable closing point ahead cannot ride on."""
    found = _first_unbookable_ahead(store)
    if found is None:
        pytest.skip("every coach has a bookable SBC -> X somewhere ahead at every halt in this dataset")
    station, coach = found
    state = on_board(store, QUERY, station, coach)
    k = kinds(state, get_successors(state, QUERY, store))
    assert k["CONTINUE"] == []


def test_continue_allowed_when_next_pair_unbookable_but_a_later_one_is(store):
    """The OLD rule (every SBC -> next pair must be bookable) was wrong: a ticket only needs its own pair."""
    stops = store.get_real_halt_stops(MAIL)
    for i, here in enumerate(stops[1:-1], start=1):
        nxt = stops[i + 1].station_code
        later = [s.station_code for s in stops[i + 2:]]
        for coach, st in store.get_availability(MAIL, "SBC", nxt).items():
            if st == "UNAVAILABLE" and coach in bookable_coaches(store, MAIL, "SBC", later):
                state = on_board(store, QUERY, here.station_code, coach)
                k = kinds(state, get_successors(state, QUERY, store))
                assert len(k["CONTINUE"]) == 1, f"{coach} at {here.station_code}: SBC->{nxt} UNAVAILABLE but a later pair is bookable"
                return
    pytest.skip("no coach in this dataset has an UNAVAILABLE next pair followed by a bookable later pair")


def test_goal_ticket_pair_is_always_bookable(store, mail_query):
    """Reaching MAS implies the closed SBC -> MAS ticket is bookable in the coach ridden."""
    from src.search_astar import astar_search
    from src.heuristic import RailHeuristic
    from src.rail_graph import build_graph
    goal, _ = astar_search(mail_query, store, RailHeuristic(build_graph(store, verbose=False)))
    assert goal is not None
    assert goal.tickets_so_far[-1].status in {"CONFIRMED", "RAC", "WAITLIST"}


def test_cycle_prevention_blocks_revisited_station(store):
    # Fabricate a closed ticket that already "used" BWT; continuing BNC->BWT must be refused.
    fake = Ticket(MAIL, "SBC", "BWT", "S1", "SL", dt.datetime(2026, 9, 16, 22, 45), dt.datetime(2026, 9, 16, 23, 48), "CONFIRMED")
    state = on_board(store, QUERY, "BNC", "S2", tickets=(fake,), transfers=1)
    assert get_successors(state, QUERY, store) == []


# --------------------------------------------------------------------------- #
# max_transfers
# --------------------------------------------------------------------------- #
def test_max_transfers_is_enforced(store):
    q0 = UserQuery("SBC", "MAS", DATE, max_transfers=0)
    state = on_board(store, q0, "BNC", "S1")
    k = kinds(state, get_successors(state, q0, store))
    assert k["TRANSFER"] == [] and len(k["CONTINUE"]) == 1

    # at the limit already: still allowed to continue, not to transfer
    q1 = UserQuery("SBC", "MAS", DATE, max_transfers=1)
    prior = Ticket(MAIL, "SBC", "BNC", "S1", "SL", dt.datetime(2026, 9, 16, 22, 45), dt.datetime(2026, 9, 16, 22, 55), "CONFIRMED")
    # pretend a transfer already happened at BNC and we are still standing there in S2
    state = on_board(store, q1, "BNC", "S2", tickets=(prior,), transfers=1)
    succ = get_successors(state, q1, store)
    assert all(s.transfer_count <= q1.max_transfers for s in succ)
    assert kinds(state, succ)["TRANSFER"] == []


# --------------------------------------------------------------------------- #
# class hard constraint
# --------------------------------------------------------------------------- #
def test_class_hard_constraint_prunes_boarding(store):
    # 12658 carries no chair car; a hard CC requirement leaves nothing to board
    q = UserQuery("SBC", "MAS", DATE, travel_class_preference="CC", class_is_hard_constraint=True)
    assert get_successors(initial_state(q), q, store) == []

    q_sl = UserQuery("SBC", "MAS", DATE, travel_class_preference="SL", class_is_hard_constraint=True)
    succ = get_successors(initial_state(q_sl), q_sl, store)
    assert succ and all(s.current_class == "SL" for s in succ)


def test_class_hard_constraint_prunes_transfers_and_mismatched_continue(store):
    q = UserQuery("SBC", "MAS", DATE, travel_class_preference="3A", class_is_hard_constraint=True)
    # on board in 3A at BNC: may continue, may only transfer within 3A
    state = on_board(store, q, "BNC", "B1")
    succ = get_successors(state, q, store)
    assert all(s.current_class == "3A" for s in succ)
    # on board in SL with a hard 3A constraint is an illegal state: no successors at all
    bad = on_board(store, q, "BNC", "S1")
    assert get_successors(bad, q, store) == []


def test_soft_class_preference_does_not_prune(store):
    q = UserQuery("SBC", "MAS", DATE, travel_class_preference="1A", class_is_hard_constraint=False)
    succ = get_successors(initial_state(q), q, store)
    assert {s.current_class for s in succ if s.current_train == MAIL} == {"SL", "3A", "2A", "1A"}
