"""Local web UI. Run `python web.py`, then visit http://localhost:8000."""
from __future__ import annotations

import argparse
import json
import logging
import threading
from dataclasses import asdict
from datetime import date as date_cls
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from cli import build_system, resolve_timed, parse_date, validate_station, parse_class
from src import railradar
from src.booking_store import BookingError
from src.demo_scenarios import ensure_database
from src.demo_window import WINDOW_DAYS
from src.live_cache import MISSING, live_trains_cache
from src.live_trains import LiveLookupError, fetch_between_stations
from src.state import UserQuery

WEB = Path(__file__).parent / "web"
# Live lookups reach a third-party site; --offline turns them off.
LIVE_LOOKUPS = True

#: Serialises everything that touches the engine or either database.
#:
#: The server is threaded (see :func:`main`) so that one idle browser
#: connection cannot starve every other request, but the System behind it is
#: NOT thread-safe: one sqlite connection each for railway.db and bookings.db,
#: one heuristic cache, one coordinator. Holding this lock for the duration of
#: an API call keeps the old "one request at a time" behaviour for real work
#: while letting sockets be accepted in parallel. Static files are served
#: outside the lock, so the page still paints while a search is running.
ENGINE_LOCK = threading.Lock()


def search(system, payload):
    """Validate browser input and expose only presentation data, not search states."""
    if not isinstance(payload, dict):
        raise ValueError("Please provide a journey query.")
    origin = validate_station(str(payload.get("origin", "")), system.store)
    destination = validate_station(str(payload.get("destination", "")), system.store)
    if origin == destination:
        raise ValueError("Choose two different stations for your journey.")
    date = parse_date(str(payload.get("date", "")), system.store)
    travel_class = parse_class(str(payload.get("travel_class", "")))
    transfers = payload.get("max_transfers", 2)
    if type(transfers) is not int or not 0 <= transfers <= 2:
        raise ValueError("Choose between zero and two transfers.")
    query = UserQuery(origin, destination, date, travel_class,
                      travel_class is not None, transfers, 1)
    rec, seconds = resolve_timed(system, query)
    options = []
    for i, option in enumerate(rec.ranked_options):
        candidate = option.candidate
        tickets = []
        for ticket in candidate.tickets:
            item = asdict(ticket)
            train = system.store.get_train(ticket.train_number)
            item["train_name"] = train.name if train else ticket.train_number
            # Berths left in THIS coach on THIS date, so the interface can show a
            # reservation landing: the count drops on the date you booked and is
            # untouched on every other date in the window.
            item["seats_remaining"], item["seats_total"] = _berths(system, ticket, date)
            tickets.append(item)
        options.append({"id": i, "tickets": tickets, "transfers": candidate.transfer_count,
                        "risky": candidate.is_risky, "score": option.final_score,
                        "fare_imputed": option.fare_imputed,
                        "fare": asdict(option.fare_time_score),
                        "comfort": asdict(option.transfer_score)})
    return {"options": options, "seconds": seconds, "failure_reason": rec.failure_reason,
            "considered": rec.total_candidates_considered,
            "duplicates": rec.duplicate_candidates_merged,
            "pruned": rec.candidates_pruned_by_hard_constraint,
            "effort": [asdict(e) for e in rec.search_effort_summary]}


def _berths(system, ticket, date):
    """``(remaining, total)`` for a ticket's coach on its travel date.

    Returns ``(None, None)`` when the coach is unknown or the segment has no
    capacity data, which the interface renders as "berths unknown" rather than
    inventing a number.
    """
    if ticket.coach is None:
        return None, None
    try:
        total = system.store.coach_capacity(ticket.train_number, ticket.coach)
        remaining = system.booking_service.seats_remaining(
            ticket.train_number, date.isoformat(),
            ticket.from_station, ticket.to_station, ticket.coach)
    except (BookingError, ValueError):
        return None, None
    return remaining, total


def _local_direct(system, origin, destination):
    """Direct trains from the bundled timetable. Always available, 2020 vintage."""
    return [{"train_number": r.train_number, "train_name": r.train_name or r.train_number,
             "from_code": origin, "to_code": destination,
             "departure": r.from_departure[:5], "arrival": r.to_arrival[:5],
             "day_offset": (r.to_day - r.from_day)
                           if r.from_day is not None and r.to_day is not None else None,
             "duration_minutes": r.duration_minutes, "halts": r.stops_between,
             "running_days": None}
            for r in system.store.get_direct_trains(origin, destination)]


def _live_direct(origin, destination, when):
    """Direct trains from the best available live provider.

    RailRadar is tried first when a key is configured: it is a documented API
    that uses current station codes. erail.in is the keyless fallback. Returns
    the trains, the number hidden because they run on other days, and the name
    of the provider that answered.

    A provider that answers with no trains has answered: the pair has no
    direct service. Only a provider that fails is skipped, so a working live
    source is never overridden by the 2020 snapshot.

    Successful answers are cached for :data:`src.live_cache.DEFAULT_TTL_SECONDS`.
    The interface searches as soon as it loads, and RailRadar's free plan is
    1,000 requests a MONTH, so repeating a lookup for the same pair and date
    must not spend the quota again.
    """
    key = (origin, destination, when.isoformat() if when else None)
    cached = live_trains_cache.get(key)
    if cached is not MISSING:
        payload, other_days, provider = cached
        return payload, other_days, f"{provider} (cached)"

    problems = []
    for name, call in (("railradar", lambda: railradar.fetch_between_stations(origin, destination, when)),
                       ("erail", lambda: fetch_between_stations(origin, destination))):
        if name == "railradar" and not railradar.is_configured():
            continue
        try:
            trains = call()
        except LiveLookupError as exc:
            problems.append(str(exc))
            continue
        running = [t for t in trains if when is None or t.runs_on(when)]
        payload = [{"train_number": t.number, "train_name": t.name,
                    "from_code": t.from_code, "to_code": t.to_code,
                    "departure": t.departure, "arrival": t.arrival,
                    "day_offset": None, "duration_minutes": t.duration_minutes,
                    "halts": t.halts, "distance_km": t.distance_km,
                    "running_days": [d[:3] for d in t.running_day_names()]}
                   for t in running]
        answer = (payload, len(trains) - len(running), t_provider(trains))
        live_trains_cache.set(key, answer)   # only successes are cached
        return answer
    # dict.fromkeys keeps order while dropping providers that failed alike.
    raise LiveLookupError(" ".join(dict.fromkeys(problems)) or "No live provider is available.")


def t_provider(trains):
    """Which provider produced these records."""
    return trains[0].provider if trains else "unknown"


def direct_trains(system, payload):
    """Every train that actually runs origin -> destination without a change.

    Prefers the live timetable, which reflects services running now, and falls
    back to the bundled 2020 snapshot whenever the live lookup fails. The
    response says which source answered so the interface never implies the
    local snapshot is current.

    Unlike :func:`search`, neither path is limited to the six trains that have
    simulated seat and run-date data.
    """
    if not isinstance(payload, dict):
        raise ValueError("Please provide a journey query.")
    origin = validate_station(str(payload.get("origin", "")), system.store)
    destination = validate_station(str(payload.get("destination", "")), system.store)
    if origin == destination:
        raise ValueError("Choose two different stations for your journey.")

    # Parsed directly rather than via parse_date(): that helper also enforces
    # the demo run-date window, which exists only because the simulated
    # availability data covers those days. Live running-days work for any date.
    when, raw_date = None, str(payload.get("date", "")).strip()
    if raw_date:
        try:
            when = date_cls.fromisoformat(raw_date)
        except ValueError:
            raise ValueError("Enter the travel date as YYYY-MM-DD.") from None

    if LIVE_LOOKUPS:
        try:
            trains, other_days, provider = _live_direct(origin, destination, when)
            return {"trains": trains, "source": "live", "provider": provider,
                    "notice": None, "other_days": other_days,
                    "origin": origin, "destination": destination}
        except LiveLookupError as exc:
            notice = str(exc)
        except Exception:
            logging.exception("Live timetable lookup failed")
            notice = "The live timetable could not be read."
    else:
        notice = None

    return {"trains": _local_direct(system, origin, destination), "source": "local",
            "provider": "bundled timetable", "notice": notice, "other_days": 0,
            "origin": origin, "destination": destination}


# --------------------------------------------------------------------------- #
# Bookings
#
# Availability in railway.db is a date-less BASELINE; a reservation is the
# per-date layer on top of it. These endpoints are the CRUD over that layer,
# and because the search reads through the same merge, a booking made here is
# immediately visible to the ranked options for that date -- and only that date.
# --------------------------------------------------------------------------- #
def meta(system):
    """Station list, the rolling booking window, and how many trains are covered."""
    window = system.store.get_window()
    return {"stations": [{"code": code, "name": name}
                         for code, name in system.store.get_bookable_stations()],
            "dates": system.store.get_run_date_range(),
            "window": {"start": window[0], "end": window[1]} if window else None,
            "window_days": WINDOW_DAYS,
            "trains": len(system.store.get_demo_train_numbers())}


def _booking_json(booking):
    return {"id": booking.id, "train_number": booking.train_number,
            "run_date": booking.run_date, "from_station": booking.from_station,
            "to_station": booking.to_station, "coach_code": booking.coach_code,
            "travel_class": booking.travel_class,
            "passenger_count": booking.passenger_count,
            "passenger_name": booking.passenger_name, "status": booking.status,
            "created_at": booking.created_at}


def list_bookings(system, params):
    """Active reservations, newest first; ``status=`` (empty) includes cancelled."""
    status = params.get("status", ["ACTIVE"])[0]
    rows = system.bookings.list(
        train_number=params.get("train_number", [None])[0],
        run_date=params.get("run_date", [None])[0],
        status=status or None,
        limit=100,
    )
    return {"bookings": [_booking_json(b) for b in rows]}


def availability(system, params):
    """Effective availability for one segment on one date: baseline plus bookings."""
    try:
        train = params["train_number"][0]
        run_date = params["run_date"][0]
        origin = validate_station(params["from_station"][0], system.store)
        destination = validate_station(params["to_station"][0], system.store)
    except (KeyError, IndexError):
        raise ValueError("Give train_number, run_date, from_station and to_station.") from None

    coaches = system.store.get_availability(train, origin, destination, run_date)
    if not coaches:
        raise ValueError(f"No availability data for {train} {origin} -> {destination}.")
    remaining = {c: system.booking_service.seats_remaining(train, run_date, origin, destination, c)
                 for c in coaches}
    return {"train_number": train, "run_date": run_date, "from_station": origin,
            "to_station": destination, "coaches": coaches, "seats_remaining": remaining}


def create_booking(system, payload):
    """Reserve berths on one leg. Invalid requests raise BookingError -> HTTP 400."""
    if not isinstance(payload, dict):
        raise ValueError("Please provide a booking.")
    count = payload.get("passenger_count", 1)
    if type(count) is not int or not 1 <= count <= 200:
        raise ValueError("Choose between one and two hundred passengers.")
    name = str(payload.get("passenger_name", "") or "").strip()[:120] or None
    booking = system.booking_service.book(
        train_number=str(payload.get("train_number", "")),
        run_date=str(payload.get("run_date", "")),
        from_station=str(payload.get("from_station", "")),
        to_station=str(payload.get("to_station", "")),
        coach_code=str(payload.get("coach_code", "")),
        passenger_count=count,
        passenger_name=name,
    )
    return {"booking": _booking_json(booking)}


def book_itinerary(system, payload):
    """Reserve every leg of one ranked itinerary in a single call.

    The interface books a whole journey, not a leg: a split itinerary is only
    useful if both of its tickets exist. If a later leg cannot be reserved the
    earlier ones are cancelled again, so a half-booked journey is never left
    behind -- the closest a demo gets to a transaction across legs.
    """
    if not isinstance(payload, dict):
        raise ValueError("Please provide an itinerary.")
    legs = payload.get("legs")
    if not isinstance(legs, list) or not 1 <= len(legs) <= 4:
        raise ValueError("An itinerary needs between one and four legs.")
    run_date = str(payload.get("run_date", ""))
    name = str(payload.get("passenger_name", "") or "").strip()[:120] or None

    made = []
    try:
        for leg in legs:
            if not isinstance(leg, dict):
                raise ValueError("Each leg must describe a train, coach and stations.")
            made.append(system.booking_service.book(
                train_number=str(leg.get("train_number", "")),
                run_date=run_date,
                from_station=str(leg.get("from_station", "")),
                to_station=str(leg.get("to_station", "")),
                coach_code=str(leg.get("coach", "") or ""),
                passenger_count=1,
                passenger_name=name,
            ))
    except (BookingError, ValueError):
        for booking in made:  # roll back, so no partial journey survives
            system.bookings.cancel(booking.id)
        raise
    return {"bookings": [_booking_json(b) for b in made]}


def update_booking(system, booking_id, payload):
    if not isinstance(payload, dict):
        raise ValueError("Please provide the fields to change.")
    count = payload.get("passenger_count")
    if count is not None and (type(count) is not int or not 1 <= count <= 200):
        raise ValueError("Choose between one and two hundred passengers.")
    booking = system.booking_service.rebook(
        booking_id, passenger_count=count, status=payload.get("status"))
    return {"booking": _booking_json(booking)}


def cancel_booking(system, booking_id):
    """Soft delete: the berths are freed, the row is kept for the audit trail."""
    if system.bookings.get(booking_id) is None:
        raise KeyError(booking_id)
    return {"booking": _booking_json(system.bookings.cancel(booking_id))}


def make_handler(system):
    class Handler(BaseHTTPRequestHandler):
        def send(self, status, data, content_type="application/json"):
            body = json.dumps(data, default=str, allow_nan=False).encode() if content_type == "application/json" else data
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                # The browser navigated away or reloaded mid-response. Routine,
                # and not worth a traceback: the default handler prints a full
                # stack for it, which looks like a server fault during a demo.
                logging.debug("client closed the connection during %s", self.path)

        def _read_payload(self):
            length = int(self.headers.get("Content-Length", 0))
            if not 0 < length <= 8192:
                raise ValueError("Invalid request size.")
            return json.loads(self.rfile.read(length))

        def _run(self, work, *, failure="We couldn't complete that request. Please try again."):
            """Run one endpoint, mapping its exceptions onto status codes.

            BookingError subclasses ValueError, so an invalid reservation is a
            clean 400 with its own message rather than a 500.
            """
            try:
                with ENGINE_LOCK:
                    result = work()
            except KeyError as exc:
                return self.send(404, {"error": f"No booking with id {exc.args[0]!r}."})
            except (ValueError, TypeError) as exc:
                return self.send(400, {"error": str(exc)})
            except Exception:
                logging.exception("Request failed: %s", self.path)
                return self.send(500, {"error": failure})
            self.send(200, result)

        def do_HEAD(self):
            """Same headers as GET, no body. Without this the default handler
            answers 501, which shows up as an error line for any client (or
            preview tool) that probes with HEAD before fetching."""
            path = urlparse(self.path).path
            if path in STATIC_FILES or path.startswith("/api/"):
                self.send_response(200)
                self.send_header("Content-Type", STATIC_FILES.get(path, ("", "application/json"))[1])
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
            else:
                self.send_response(404)
                self.end_headers()

        def do_GET(self):
            parsed = urlparse(self.path)
            path, params = parsed.path, parse_qs(parsed.query)
            if path == "/api/meta":
                return self._run(lambda: meta(system))
            if path == "/api/bookings":
                return self._run(lambda: list_bookings(system, params))
            if path == "/api/availability":
                return self._run(lambda: availability(system, params))
            if path not in STATIC_FILES:
                return self.send(404, {"error": "Not found"})
            filename, mime = STATIC_FILES[path]
            self.send(200, (WEB / filename).read_bytes(), mime)

        def do_POST(self):
            path = urlparse(self.path).path
            handlers = {"/api/search": search, "/api/direct": direct_trains,
                        "/api/bookings": create_booking, "/api/book-itinerary": book_itinerary}
            if path not in handlers:
                return self.send(404, {"error": "Not found"})
            self._run(lambda: handlers[path](system, self._read_payload()))

        def do_PATCH(self):
            booking_id = _booking_id(self.path)
            if booking_id is None:
                return self.send(404, {"error": "Not found"})
            self._run(lambda: update_booking(system, booking_id, self._read_payload()))

        def do_DELETE(self):
            booking_id = _booking_id(self.path)
            if booking_id is None:
                return self.send(404, {"error": "Not found"})
            self._run(lambda: cancel_booking(system, booking_id))
    return Handler


#: Files served from web/, by URL path. An allow-list, so no request can
#: address a file outside this directory.
STATIC_FILES = {"/": ("index.html", "text/html; charset=utf-8"),
                "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                "/style.css": ("style.css", "text/css; charset=utf-8")}


def _booking_id(path: str) -> str | None:
    """``/api/bookings/ABC123`` -> ``ABC123``; anything else -> None."""
    parts = urlparse(path).path.strip("/").split("/")
    if len(parts) == 3 and parts[0] == "api" and parts[1] == "bookings" and parts[2]:
        return parts[2]
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--offline", action="store_true",
                        help="skip live erail.in lookups and use the bundled timetable only")
    args = parser.parse_args()
    global LIVE_LOOKUPS
    LIVE_LOOKUPS = not args.offline
    # Builds on first run; also rebuilds when the 10-day booking window has
    # rolled past, so the demo keeps working without anyone regenerating data.
    ensure_database()
    system = build_system(use_pool=False)
    try:
        # Threaded on purpose. With the single-threaded HTTPServer, a browser
        # that opens a speculative connection without sending a request (Chrome
        # preconnects several) blocks the accept loop reading from that idle
        # socket, and every other request -- page, script, search, booking --
        # queues behind it forever. ENGINE_LOCK keeps the actual work serial,
        # so nothing below this line has to become thread-safe.
        server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(system))
        server.daemon_threads = True  # don't let a stuck connection block Ctrl+C
        print(f"SplitRail is ready at http://localhost:{args.port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    finally:
        system.close()


if __name__ == "__main__":
    main()
