"""Local web UI. Run `python web.py`, then visit http://localhost:8000."""
from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict
from datetime import date as date_cls
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

from cli import build_system, resolve_timed, parse_date, validate_station, parse_class
from data_source.build_sqlite_db import build, DEFAULT_OUT
from src.live_trains import LiveLookupError, fetch_between_stations
from src.state import UserQuery

WEB = Path(__file__).parent / "web"
# Live lookups reach a third-party site; --offline turns them off.
LIVE_LOOKUPS = True


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
    """Direct trains from erail.in, limited to those running on ``when``.

    Returns the trains plus the number hidden because they run on other days,
    so the interface can say why the list is shorter than the full week's.
    """
    trains = fetch_between_stations(origin, destination)
    running = [t for t in trains if when is None or t.runs_on(when)]
    return [{"train_number": t.number, "train_name": t.name,
             "from_code": t.from_code, "to_code": t.to_code,
             "departure": t.departure, "arrival": t.arrival,
             "day_offset": None, "duration_minutes": t.duration_minutes,
             "halts": None,
             "running_days": [d[:3] for d in t.running_day_names()]}
            for t in running], len(trains) - len(running)


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
            trains, other_days = _live_direct(origin, destination, when)
            return {"trains": trains, "source": "live", "notice": None,
                    "other_days": other_days, "origin": origin, "destination": destination}
        except LiveLookupError as exc:
            notice = str(exc)
        except Exception:
            logging.exception("Live timetable lookup failed")
            notice = "The live timetable could not be read."
    else:
        notice = None

    return {"trains": _local_direct(system, origin, destination), "source": "local",
            "notice": notice, "other_days": 0,
            "origin": origin, "destination": destination}


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
            self.wfile.write(body)

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/api/meta":
                stations = [{"code": code, "name": name}
                            for code, name in system.store.get_bookable_stations()]
                return self.send(200, {"stations": stations, "dates": system.store.get_run_date_range(),
                                       "trains": len(system.store.get_demo_train_numbers())})
            files = {"/": ("index.html", "text/html; charset=utf-8"),
                     "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                     "/style.css": ("style.css", "text/css; charset=utf-8")}
            if path not in files:
                return self.send(404, {"error": "Not found"})
            filename, mime = files[path]
            self.send(200, (WEB / filename).read_bytes(), mime)

        def do_POST(self):
            handlers = {"/api/search": search, "/api/direct": direct_trains}
            if self.path not in handlers:
                return self.send(404, {"error": "Not found"})
            try:
                length = int(self.headers.get("Content-Length", 0))
                if not 0 < length <= 8192:
                    raise ValueError("Invalid request size.")
                payload = json.loads(self.rfile.read(length))
                result = handlers[self.path](system, payload)
            except (ValueError, TypeError) as exc:
                return self.send(400, {"error": str(exc)})
            except Exception:
                logging.exception("Journey search failed")
                return self.send(500, {"error": "We couldn't finish this search. Please try again."})
            self.send(200, result)
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--offline", action="store_true",
                        help="skip live erail.in lookups and use the bundled timetable only")
    args = parser.parse_args()
    global LIVE_LOOKUPS
    LIVE_LOOKUPS = not args.offline
    if not DEFAULT_OUT.exists():
        print("Building the local database from included demo data…")
        build(DEFAULT_OUT)
    system = build_system(use_pool=False)
    try:
        server = HTTPServer(("127.0.0.1", args.port), make_handler(system))
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
