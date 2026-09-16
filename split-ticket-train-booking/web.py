"""Local web UI. Run `python web.py`, then visit http://localhost:8000."""
from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

from cli import build_system, resolve_timed, parse_date, validate_station, parse_class
from data_source.build_sqlite_db import build, DEFAULT_OUT
from src.state import UserQuery

WEB = Path(__file__).parent / "web"


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
                subset = json.loads((WEB.parent / "data_source/demo_subset.json").read_text())
                codes = {s["station_code"] for stops in subset["schedules"].values() for s in stops
                         if s.get("halt_minutes", 0) or s.get("arrival") is None or s.get("departure") is None}
                stations = [{"code": code, "name": system.store.get_station(code).name}
                            for code in sorted(codes) if system.store.get_station(code)]
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
            if self.path != "/api/search":
                return self.send(404, {"error": "Not found"})
            try:
                length = int(self.headers.get("Content-Length", 0))
                if not 0 < length <= 8192:
                    raise ValueError("Invalid request size.")
                payload = json.loads(self.rfile.read(length))
                result = search(system, payload)
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
    args = parser.parse_args()
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
