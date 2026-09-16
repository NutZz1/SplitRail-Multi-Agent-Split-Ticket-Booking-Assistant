"""
FastAPI entry point: JSON API over the existing engine, plus (when built)
the React client as static files -- one Python process serves everything.

    uvicorn api.main:app --reload            # development (API only, React via Vite on :5173)
    uvicorn api.main:app --port 8000         # production: also serves client/dist at /

Environment:
    SPLIT_TICKET_POOL=1     run searches in the 2-process worker pool (default: in-process)
    SPLIT_TICKET_DB=path    railway.db location (default: alongside this project)

This layer imports and calls the unmodified CoordinatorAgent / RailDataStore.
It validates INPUT at the boundary (HTTP 400) and serializes OUTPUT
(api/schemas.py). It adds no search, scoring, or coordination logic.
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.agents import coordinator_agent as coord_mod  # noqa: E402
from src.demo_scenarios import (  # noqa: E402  (shared with cli.py -- one source of truth)
    DEMO_SCENARIOS,
    InputError,
    build_system,
    demo_trains,
    parse_class,
    resolve_timed_async,
    validate_date,
    validate_station,
)
from src.state import UserQuery  # noqa: E402

from api.schemas import (  # noqa: E402
    DemoScenarioOut,
    DemoTrainOut,
    HealthOut,
    SearchRequest,
    SearchResponse,
    StationOut,
)

log = logging.getLogger("split_ticket.api")

CLIENT_DIST = PROJECT_ROOT / "client" / "dist"

#: Origins allowed to call the API cross-origin: the Vite dev server only.
#: In production the React build is served by this same process, so no
#: cross-origin call happens at all.
DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]

WEIGHTS = {
    "W_TIME": coord_mod.W_TIME,
    "W_FARE": coord_mod.W_FARE,
    "W_TRANSFER": coord_mod.W_TRANSFER,
    "W_LAYOVER": coord_mod.W_LAYOVER,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Build the engine ONCE (store, graph + heuristic, coordinator) and keep it on app.state."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    use_pool = os.environ.get("SPLIT_TICKET_POOL", "0") == "1"
    db_path = os.environ.get("SPLIT_TICKET_DB") or None
    log.info("Setting up: opening railway.db, building the station graph and heuristic%s ...",
             ", warming the worker pool" if use_pool else "")
    system = build_system(db_path, use_pool=use_pool)
    g = system.heuristic._graph
    log.info("Ready in %.1f s (graph: %s stations, %s edges; searches run %s).",
             system.setup_seconds, f"{g.number_of_nodes():,}", f"{g.number_of_edges():,}",
             "in 2 worker processes" if system.pool else "in-process")
    app.state.system = system
    try:
        yield
    finally:
        system.close()


app = FastAPI(title="Split-ticket train booking assistant", version="1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=DEV_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


# --------------------------------------------------------------------------- #
# API
#
# Every endpoint that touches the store is ``async def`` on purpose: FastAPI
# runs plain ``def`` endpoints in a threadpool, but the store's sqlite3
# connection is bound to the event-loop thread that created it in lifespan.
# The lookups are sub-millisecond, so serving them on the loop is fine.
# --------------------------------------------------------------------------- #
@app.get("/api/health", response_model=HealthOut)
async def health(request: Request) -> HealthOut:
    system = request.app.state.system
    g = system.heuristic._graph
    return HealthOut(
        status="ok",
        setup_time_seconds=round(system.setup_seconds, 3),
        graph_nodes=g.number_of_nodes(),
        graph_edges=g.number_of_edges(),
        run_date_range=system.store.get_run_date_range(),
        worker_pool=system.pool is not None,
    )


@app.get("/api/stations", response_model=list[StationOut])
async def stations(request: Request, query: str = Query("", max_length=64), limit: int = Query(20, ge=1, le=50)) -> list[StationOut]:
    """Autocomplete: stations whose code or name contains ``query`` (case-insensitive)."""
    store = request.app.state.system.store
    return [StationOut.from_domain(s) for s in store.search_stations(query, limit=limit)]


@app.get("/api/demo-trains", response_model=list[DemoTrainOut])
async def demo_trains_endpoint(request: Request) -> list[DemoTrainOut]:
    """The trains with real availability / run-date coverage (same data as ``cli.py --list-trains``)."""
    return [DemoTrainOut(**t) for t in demo_trains(request.app.state.system.store)]


@app.get("/api/demo-scenarios", response_model=list[DemoScenarioOut])
async def demo_scenarios_endpoint() -> list[DemoScenarioOut]:
    """The four scripted scenarios ``cli.py --demo`` runs, as ready-to-submit requests."""
    return [DemoScenarioOut.from_domain(sc) for sc in DEMO_SCENARIOS]


@app.post("/api/search", response_model=SearchResponse)
async def search(request: Request, body: SearchRequest) -> SearchResponse:
    """Validate the request at the boundary, then await CoordinatorAgent.resolve() and return ALL of it."""
    system = request.app.state.system
    store = system.store
    try:
        origin = validate_station(body.origin_station, store)
        destination = validate_station(body.destination_station, store)
        travel_date = validate_date(body.travel_date, store)
        travel_class = parse_class(body.travel_class_preference)
        if body.class_is_hard_constraint and travel_class is None:
            raise InputError("A hard class requirement needs a class preference.")
        query = UserQuery(
            origin_station=origin,
            destination_station=destination,
            travel_date=travel_date,
            travel_class_preference=travel_class,
            class_is_hard_constraint=body.class_is_hard_constraint,
            max_transfers=body.max_transfers,
            passenger_count=body.passenger_count,
        )
    except (InputError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    rec, seconds = await resolve_timed_async(system, query)   # agent exceptions propagate (HTTP 500)
    def name_of(train_number: str) -> str:
        t = store.get_train(train_number)
        return t.name if t else ""

    return SearchResponse.from_domain(rec, seconds, WEIGHTS, name_of=name_of)


# --------------------------------------------------------------------------- #
# React build (only when it exists, so the API runs standalone in development)
# --------------------------------------------------------------------------- #
if CLIENT_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=CLIENT_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def client(path: str):
        """Serve the built React app; unknown paths fall back to index.html (client-side routing)."""
        candidate = CLIENT_DIST / path
        if path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(CLIENT_DIST / "index.html")
else:

    @app.get("/", include_in_schema=False)
    def root():
        return {"message": "API is running. Build the React client (client/dist) to serve the UI from here; "
                           "see /docs for the API."}
