"""
Split-ticket train booking assistant -- command-line entry point.

    python cli.py                  interactive: prompts for a query, prints every ranked option
    python cli.py --demo           scripted walkthrough of the four established scenarios
    python cli.py --demo-bookings  scripted CRUD walkthrough over the rolling booking window
    python cli.py --list-trains    the demo trains with real availability / run-date coverage
    python cli.py --no-pool        run searches in-process instead of the worker-process pool

This is a thin wrapper around CoordinatorAgent.resolve(): it validates user
INPUT at the boundary and formats the OUTPUT. It adds no search, scoring or
coordination logic, and it does not catch exceptions raised inside the
agents -- a genuine bug should surface with its real traceback.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.agents.coordinator_agent import SAME_TRAIN_MAX_TRANSFERS_CAP  # noqa: E402
from src.data_store import RailDataStore  # noqa: E402
from src.demo_scenarios import (  # noqa: E402  (shared with web.py -- one source of truth)
    VALID_CLASSES,
    InputError,
    System,
    build_system,
    demo_scenarios,
    demo_trains,
    ensure_database,
    parse_class,
    parse_date,
    resolve_timed,
    validate_station,
)
from src.demo_window import WINDOW_DAYS, window_dates  # noqa: E402
from src.final_recommendation import FinalRecommendation, RankedItinerary  # noqa: E402
from src.state import UserQuery  # noqa: E402


# --------------------------------------------------------------------------- #
# Input validation (CLI boundary only; shared validators live in src.demo_scenarios)
# --------------------------------------------------------------------------- #
def parse_int(text: str, default: int, minimum: int, label: str) -> int:
    text = text.strip()
    if not text:
        return default
    try:
        value = int(text)
    except ValueError:
        raise InputError(f"{label} must be a whole number.")
    if value < minimum:
        raise InputError(f"{label} must be >= {minimum}.")
    return value


# --------------------------------------------------------------------------- #
# Output formatting
# --------------------------------------------------------------------------- #
def format_option(index: int, option: RankedItinerary) -> str:
    c, f, t = option.candidate, option.fare_time_score, option.transfer_score
    tag = "  <-- RECOMMENDED" if index == 1 else ""
    kind = {"SameTrainSearchAgent": "same-train", "DifferentTrainSearchAgent": "different-train"}.get(c.agent_name, c.agent_name)
    lines = [f"  #{index}  final_score {option.final_score:,.1f}   [{kind}]{tag}"]
    for i, tk in enumerate(c.tickets, 1):
        status = f", {tk.status}" if tk.status else ""
        lines.append(
            f"       leg {i}: train {tk.train_number}  {tk.from_station} -> {tk.to_station}  "
            f"coach {tk.coach} ({tk.travel_class}{status})  "
            f"dep {tk.boarding_datetime:%a %d %b %H:%M}  arr {tk.alighting_datetime:%a %d %b %H:%M}"
        )
    fare = f"Rs {f.total_fare:,.2f}" if f.total_fare is not None else "unavailable (imputed for ranking)"
    lines.append(
        f"       transfers {c.transfer_count} | fare {fare} | moving {f.moving_time_minutes:.0f} min | "
        f"wall-clock {f.wall_clock_minutes:.0f} min | layover/dwell {f.layover_minutes:.0f} min"
    )
    if t.per_transfer_penalties:
        for p in t.per_transfer_penalties:
            dist = "n/a (different rake)" if p["coach_distance"] is None and p["kind"] == "different_train" else p["coach_distance"]
            lines.append(
                f"       transfer at {p['station']} ({p['kind'].replace('_', '-')}, {p['train_from']} -> {p['train_to']}, "
                f"coach {p['coach_from']} -> {p['coach_to']}): coach distance {dist}, "
                f"night {'yes' if p['is_night'] else 'no'} (boards {p['boarding_time']}), penalty {p['penalty']:.0f}"
                + (f"  [{p['note']}]" if p.get("note") else "")
            )
        lines.append(f"       transfer feasibility penalty total: {t.total_feasibility_penalty:.0f}")
    else:
        lines.append("       transfer feasibility penalty total: 0 (no transfers)")
    return "\n".join(lines)


def format_recommendation(rec: FinalRecommendation, seconds: float) -> str:
    q = rec.query
    head = (f"{q.origin_station} -> {q.destination_station} on {q.travel_date:%a %Y-%m-%d}"
            f" | class {q.travel_class_preference or 'any'}{' (hard)' if q.class_is_hard_constraint else ''}"
            f" | max transfers {q.max_transfers} | passengers {q.passenger_count}")
    out = [head, "-" * len(head)]
    if not rec.found:
        out.append("NO ITINERARY FOUND")
        out.append(f"  reason: {rec.failure_reason}")
        for e in rec.search_effort_summary:
            out.append(f"  search effort: {e.agent_name} searched {e.nodes_expanded} state(s) before concluding")
        out.append(f"  candidates considered before giving up: {rec.total_candidates_considered}")
    else:
        out.append(
            f"{len(rec.ranked_options)} option(s)  "
            f"({rec.total_candidates_considered} candidates considered, "
            f"{rec.duplicate_candidates_merged} duplicate(s) merged, "
            f"{rec.candidates_pruned_by_hard_constraint} pruned by hard constraints)"
        )
        for i, option in enumerate(rec.ranked_options, 1):
            out.append(format_option(i, option))
    out.append(f"resolve() took {seconds * 1000:.0f} ms")
    return "\n".join(out)


def format_demo_trains(store: RailDataStore) -> str:
    lo, hi = store.get_run_date_range() or ("?", "?")
    lines = [
        "Trains with real seat-availability and run-date coverage (queries outside",
        f"this set correctly find nothing). The booking window covers {lo} to {hi}",
        f"({WINDOW_DAYS} days from today) and rolls forward daily.",
        "",
        f"  {'train':<7}{'name':<48}{'from':<7}{'to':<7}runs on",
    ]
    for t in demo_trains(store):
        runs = "daily" if t["runs_daily"] else "/".join(t["runs_on"])
        lines.append(f"  {t['number']:<7}{t['name'][:46]:<48}{t['from_station']:<7}{t['to_station']:<7}{runs}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Scripted demo
# --------------------------------------------------------------------------- #
def demo_labels() -> tuple[tuple[str, UserQuery, str], ...]:
    """(label, query, expectation) for each scenario, anchored to today's window.

    Built on call rather than at import so the scenarios follow the rolling
    window instead of freezing whichever one existed when the module loaded.
    """
    return tuple((f"{sc.id}. {sc.name}", sc.query, sc.description) for sc in demo_scenarios())


def run_demo(system: System) -> list[tuple[str, FinalRecommendation, float]]:
    """Run every scripted scenario; returns (label, recommendation, seconds) in order."""
    return [(label, *resolve_timed(system, query)) for label, query, _ in demo_labels()]


def print_demo(system: System) -> None:
    print(format_demo_trains(system.store))
    print()
    for (label, _, expectation), (_, rec, secs) in zip(demo_labels(), run_demo(system)):
        print("=" * 78)
        print(label)
        print(f"   {expectation}")
        print("=" * 78)
        print(format_recommendation(rec, secs))
        print()


# --------------------------------------------------------------------------- #
# Scripted booking demo (CRUD against the rolling window)
# --------------------------------------------------------------------------- #
def _first_leg(rec: FinalRecommendation):
    """The first ticket of the best option, or None if nothing was found."""
    return rec.ranked_options[0].candidate.tickets[0] if rec.ranked_options else None


def _leg_line(rec: FinalRecommendation) -> str:
    leg = _first_leg(rec)
    if leg is None:
        return f"no itinerary ({rec.failure_reason})"
    return (f"{leg.train_number} {leg.from_station}->{leg.to_station} "
            f"coach {leg.coach} ({leg.travel_class}, {leg.status})")


def print_booking_demo(system: System) -> None:
    """Show that booking is a real mutation the search algorithms can see.

    Books out one coach on ONE date, then re-runs the identical search on
    that date and on the next one the train runs. The first changes, the
    second does not -- which is the whole point of keying reservations by
    run date. Finally the booking is cancelled and the first search returns
    to its original answer, demonstrating delete as well as create.
    """
    service, bookings, store = system.booking_service, system.bookings, system.store
    assert service is not None and bookings is not None
    lo, hi = store.get_run_date_range() or ("?", "?")
    print("=" * 78)
    print(f"BOOKING DEMO -- window {lo} .. {hi} ({WINDOW_DAYS} days, rolls forward daily)")
    print("=" * 78)

    dates = [d for d in window_dates() if store.runs_on_date("12658", d.isoformat())][:2]
    if len(dates) < 2:
        print("Need two running dates for 12658 in the window; skipping.")
        return
    day_one, day_two = dates[0], dates[1]

    query_one = UserQuery("SBC", "MAS", day_one, max_transfers=2)
    query_two = UserQuery("SBC", "MAS", day_two, max_transfers=2)
    before_one, _ = resolve_timed(system, query_one)
    before_two, _ = resolve_timed(system, query_two)
    leg = _first_leg(before_one)
    if leg is None or leg.coach is None:
        print(f"No itinerary on {day_one} to book against; skipping.")
        return

    print(f"\n1. READ  search {day_one} : {_leg_line(before_one)}")
    print(f"         search {day_two} : {_leg_line(before_two)}")

    capacity = store.coach_capacity(leg.train_number, leg.coach)
    remaining = service.seats_remaining(
        leg.train_number, day_one.isoformat(), leg.from_station, leg.to_station, leg.coach)
    print(f"\n2. CREATE booking out coach {leg.coach} on {day_one} "
          f"({remaining} of {capacity} berths free) ...")
    booking = service.book(
        train_number=leg.train_number, run_date=day_one.isoformat(),
        from_station=leg.from_station, to_station=leg.to_station,
        coach_code=leg.coach, passenger_count=remaining, passenger_name="Demo Group",
    )
    print(f"         created {booking.describe()}")

    after_one, _ = resolve_timed(system, query_one)
    after_two, _ = resolve_timed(system, query_two)
    print(f"\n3. READ  search {day_one} : {_leg_line(after_one)}   <-- changed")
    print(f"         search {day_two} : {_leg_line(after_two)}   <-- untouched, "
          f"a different run date")

    print(f"\n4. UPDATE release all but 1 berth of {booking.id} ...")
    updated = service.rebook(booking.id, passenger_count=1)
    print(f"         {updated.describe()}")
    print(f"         search {day_one} : {_leg_line(resolve_timed(system, query_one)[0])}")

    print(f"\n5. DELETE cancel {booking.id} ...")
    service.rebook(booking.id, status="CANCELLED")
    restored, _ = resolve_timed(system, query_one)
    print(f"         search {day_one} : {_leg_line(restored)}   <-- back to the original")
    print(f"\n   active bookings now: {len(bookings.list(status='ACTIVE'))}; "
          f"cancelled rows kept for audit: {len(bookings.list(status='CANCELLED'))}")
    bookings.clear()


# --------------------------------------------------------------------------- #
# Interactive loop
# --------------------------------------------------------------------------- #
def ask(prompt: str, parser, *, allow_list: RailDataStore | None = None):
    """Prompt until ``parser`` accepts the input. Typing 'list' shows the demo trains."""
    while True:
        text = input(prompt)
        if allow_list is not None and text.strip().lower() == "list":
            print(format_demo_trains(allow_list))
            continue
        try:
            return parser(text)
        except InputError as e:
            print(f"  ! {e}")


def prompt_query(store: RailDataStore) -> UserQuery:
    print("\nEnter a query (type 'list' at a station prompt to see the covered demo trains).")
    origin = ask("  origin station code: ", lambda s: validate_station(s, store), allow_list=store)
    dest = ask("  destination station code: ", lambda s: validate_station(s, store), allow_list=store)
    lo, hi = store.get_run_date_range() or ("?", "?")
    date = ask(f"  travel date YYYY-MM-DD ({lo} .. {hi}): ", lambda s: parse_date(s, store))
    cls = ask(f"  class preference [{'/'.join(VALID_CLASSES)}] (blank = any): ", parse_class)
    hard = False
    if cls is not None:
        hard = ask("  is that class a hard requirement? (y/n) [n]: ",
                   lambda s: s.strip().lower() in ("y", "yes"))
    print(f"  (note: the coordinator caps the SAME-TRAIN search to {SAME_TRAIN_MAX_TRANSFERS_CAP} transfer "
          f"regardless; your value applies to the different-train search)")
    max_transfers = ask("  max transfers [2]: ", lambda s: parse_int(s, 2, 0, "max transfers"))
    passengers = ask("  passengers [1]: ", lambda s: parse_int(s, 1, 1, "passengers"))
    return UserQuery(origin, dest, date, cls, hard, max_transfers, passengers)


def interactive(system: System) -> None:
    while True:
        query = prompt_query(system.store)
        print()
        rec, secs = resolve_timed(system, query)
        print(format_recommendation(rec, secs))
        again = input("\nRun another query? (y/n) [y]: ").strip().lower()
        if again in ("n", "no", "q", "quit", "exit"):
            break


# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Split-ticket train booking assistant")
    ap.add_argument("--demo", action="store_true", help="run the scripted walkthrough, no prompts")
    ap.add_argument("--demo-bookings", action="store_true",
                    help="scripted CRUD walkthrough: book a coach out and watch the search change")
    ap.add_argument("--list-trains", action="store_true", help="show the demo trains with real data coverage")
    ap.add_argument("--no-pool", action="store_true", help="run searches in-process (no worker processes)")
    ap.add_argument("--db", default=None, help="path to railway.db (default: alongside this script)")
    args = ap.parse_args(argv)

    # Builds on first run, and rebuilds when the booking window has rolled on.
    ensure_database(args.db)

    if args.list_trains:
        with RailDataStore(args.db) as store:
            print(format_demo_trains(store))
        return 0

    print("Setting up: opening railway.db, building the station graph and heuristic"
          + ("" if args.no_pool else ", warming the worker pool") + " ...")
    system = build_system(args.db, use_pool=not args.no_pool)
    try:
        g = system.heuristic._graph
        stats = g.graph["build_stats"]
        print(f"Ready in {system.setup_seconds:.1f} s "
              f"(graph: {g.number_of_nodes():,} stations, {g.number_of_edges():,} edges from "
              f"{stats.trains_contributing:,} trains, {stats.skipped_nonpositive} inconsistent timetable pairs skipped; "
              f"searches run {'in 2 worker processes' if system.pool else 'in-process'}).")
        if args.demo_bookings:
            print_booking_demo(system)
        elif args.demo:
            print_demo(system)
        else:
            interactive(system)
    finally:
        system.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
